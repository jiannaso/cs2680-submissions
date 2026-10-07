"""Tool schemas (what the model sees) and executors (what the harness runs)."""
import ast
import difflib
import os
import re
import subprocess

MAX_OUTPUT_CHARS = 12000
BASH_TIMEOUT_S = 300
READ_WINDOW = 400  # default number of lines read_file returns

# "    42\t" — the prefix read_file puts on each line; models sometimes copy it into old_string.
LINE_NO_PREFIX = re.compile(r"^ *\d+\t", re.MULTILINE)

# git subcommands that would move HEAD, stage, or throw away work. The patch is
# collected with `git diff` against the starting commit, so any of these can
# silently empty or corrupt it. (Global options like `-C dir` may come first.)
BLOCKED_GIT = re.compile(
    r"(?:^|[;&|(`]|\$\(|\b(?:then|do|else|sudo|xargs|env|time)\s)\s*git\s+(?:-{1,2}[\w-]+(?:[= ](?!-)\S+)?\s+)*"
    r"(add|commit|stash|reset|checkout|switch|clean|merge|rebase|cherry-pick|revert|am|pull|push)\b"
)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command and return stdout+stderr. Every call starts a fresh shell in the "
                           "repository root (a `cd` does not carry over to the next call; chain with &&). "
                           f"Times out after {BASH_TIMEOUT_S}s; no interactive input. Use for grep/find/ls, running "
                           "tests and scripts. git commands that change repository state (add, commit, stash, "
                           "reset, checkout, ...) are refused.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "the shell command"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": f"Read a file, each line prefixed with its line number and a tab. Returns at most "
                           f"{READ_WINDOW} lines per call; pass start_line/end_line (1-based, inclusive) to read "
                           "further. The line-number prefixes are not part of the file. On a directory, lists it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "path relative to the repo root (e.g. src/foo.py), "
                                                             "or an absolute path inside the repo or /tmp"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace an exact string in a file. old_string must match the file exactly (indentation "
                           "included, without read_file's line-number prefixes) and occur exactly once; include "
                           "surrounding lines to make it unique. With old_string empty, creates a new file with "
                           "new_string as its content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "path relative to the repo root, or under /tmp"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repro_check",
            "description": "Register a reproduction check: a shell command (usually `python /tmp/check_x.py`) that "
                           "exits 0 only when the issue is fixed. It runs now and its output is returned; before "
                           "your fix it should fail. done re-runs every registered check.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "command run from the repo root"},
                    "description": {"type": "string", "description": "which requirement this checks"},
                },
                "required": ["command", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Declare the task finished, after the fix is made in the repository and verified.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


def _agent_env():
    """Environment for commands the model runs. The API key stays out of it, so `env` or a
    test that dumps os.environ cannot put the key into the model's context and the logs."""
    return {k: v for k, v in os.environ.items() if k != "CS2680_API_KEY"}


class ToolError(Exception):
    pass


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep head and tail: errors and test summaries tend to be at the end."""
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n\n... [{omitted} chars truncated] ...\n\n{text[-half:]}"


class Tools:
    def __init__(self, workdir: str, task_text: str = ""):
        self.workdir = os.path.realpath(workdir)
        self.task_text = task_text  # shown again at the final review in done()
        self._reviewed = False
        self._calls_since_review = 0  # tool calls made after the final review was shown
        self._review_skip_warned = False
        self._export_warned = False
        self.budget_low = False  # set by the loop when it sends the wrap-up warning
        self._empty_done_warned = False
        self._failing_done_warned = False
        self.repro_checks = []  # (description, command)

    def _resolve(self, path: str, allow_tmp: bool = False) -> str:
        full = os.path.realpath(os.path.join(self.workdir, path))
        if full == self.workdir or full.startswith(self.workdir + os.sep):
            return full
        if allow_tmp and full.startswith("/tmp" + os.sep):
            return full
        raise ToolError(f"path {path!r} is outside the repository {self.workdir}")

    def execute(self, name: str, args: dict) -> str:
        """Run one tool. Raises ToolError for problems the model should see as errors."""
        if name != "done":
            self._calls_since_review += 1
        if name == "bash":
            return self.bash(args["command"])
        if name == "read_file":
            return self.read_file(args["path"], args.get("start_line"), args.get("end_line"))
        if name == "edit_file":
            return self.edit_file(args["path"], args["old_string"], args["new_string"])
        if name == "repro_check":
            return self.repro_check(args["command"], args.get("description", ""))
        if name == "done":
            return self.done()
        raise ToolError(f"unknown tool {name!r}")

    def bash(self, command: str) -> str:
        blocked = BLOCKED_GIT.search(command)
        if blocked:
            raise ToolError(f"`git {blocked.group(1)}` is not allowed: leave the fix as uncommitted changes in the "
                            "working tree. Use read-only git (status, diff, log, show) or edit files directly.")
        code, out = self._run(command)
        if code is None:
            raise ToolError(f"command timed out after {BASH_TIMEOUT_S}s")
        if code != 0:
            out += f"\n[exit code {code}]"
        return out or "[no output]"

    def _run(self, command: str):
        """(exit code, stdout+stderr); exit code None on timeout."""
        try:
            proc = subprocess.run(
                command, shell=True, cwd=self.workdir, capture_output=True, text=True,
                timeout=BASH_TIMEOUT_S, errors="replace", stdin=subprocess.DEVNULL, env=_agent_env(),
            )
        except subprocess.TimeoutExpired:
            return None, ""
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def repro_check(self, command: str, description: str) -> str:
        blocked = BLOCKED_GIT.search(command)
        if blocked:
            raise ToolError(f"`git {blocked.group(1)}` is not allowed in a check")
        code, out = self._run(command)
        if (description, command) not in self.repro_checks:
            self.repro_checks.append((description, command))
        status = "TIMED OUT" if code is None else ("PASSES (exit 0)" if code == 0 else f"FAILS (exit {code})")
        hint = ""
        if code == 0 and not self._repo_changed():
            hint = ("\nNote: it already passes before any fix, so it does not reproduce the issue yet. "
                    "Make it assert the expected behavior.")
        return (f"registered check #{len(self.repro_checks)}: {description}\n$ {command}\n{status}{hint}\n"
                f"{out.strip() or '[no output]'}")

    def read_file(self, path: str, start_line=None, end_line=None) -> str:
        full = self._resolve(path, allow_tmp=True)
        if os.path.isdir(full):
            entries = sorted(e + ("/" if os.path.isdir(os.path.join(full, e)) else "") for e in os.listdir(full))
            return f"{path} is a directory:\n" + "\n".join(entries)
        if not os.path.isfile(full):
            raise ToolError(f"no such file: {path}")
        with open(full, encoding="utf-8", errors="replace", newline="") as f:
            lines = f.read().splitlines()
        if not lines:
            return f"{path} is empty"
        start = max(int(start_line or 1), 1)
        if start > len(lines):
            raise ToolError(f"start_line {start} is past the end of {path} ({len(lines)} lines)")
        end = min(int(end_line or start + READ_WINDOW - 1), len(lines), start + READ_WINDOW - 1)
        body = "\n".join(f"{i:6d}\t{lines[i - 1]}" for i in range(start, end + 1))
        more = f"\n[{len(lines) - end} more lines; continue with start_line={end + 1}]" if end < len(lines) else ""
        return f"{path} (lines {start}-{end} of {len(lines)})\n{body}{more}"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        full = self._resolve(path, allow_tmp=True)
        if old_string == "":
            if os.path.exists(full):
                raise ToolError(f"{path} already exists; pass a non-empty old_string to edit it")
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as f:
                f.write(new_string)
            return f"created {path}" + self._syntax_warning(full, new_string) + self._test_import_hint(full)
        if not os.path.isfile(full):
            raise ToolError(f"no such file: {path}")
        # newline="" keeps \r\n files as they are, so an edit does not rewrite every line ending.
        with open(full, encoding="utf-8", newline="") as f:
            content = f.read()
        crlf = "\r\n" in content
        if crlf:
            old_string = old_string.replace("\r\n", "\n").replace("\n", "\r\n")
            new_string = new_string.replace("\r\n", "\n").replace("\n", "\r\n")

        count = content.count(old_string)
        note = ""
        if count == 0 and LINE_NO_PREFIX.search(old_string):
            # The model copied read_file's line numbers; strip them from both sides and retry.
            stripped = LINE_NO_PREFIX.sub("", old_string)
            if content.count(stripped) == 1:
                old_string, new_string = stripped, LINE_NO_PREFIX.sub("", new_string)
                count, note = 1, " (removed read_file line-number prefixes from old_string/new_string)"
        if count == 0:
            raise ToolError(f"old_string not found in {path}. Copy the text exactly (indentation matters, no "
                            f"line-number prefixes).{self._closest(content, old_string)}")
        if count > 1:
            raise ToolError(f"old_string occurs {count} times in {path}; include more surrounding lines to make it unique")

        new_content = content.replace(old_string, new_string, 1)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)
        first = content[:content.index(old_string)].count("\n") + 1
        return f"edited {path}{note}\n{self._snippet(new_content, first, new_string)}" + \
            self._syntax_warning(full, new_content)

    @staticmethod
    def _snippet(content: str, first: int, new_string: str, context: int = 3) -> str:
        """The edited region with a few lines of context, so the model can check the result."""
        lines = content.splitlines()
        last = first + max(new_string.count("\n"), 0)
        lo, hi = max(first - context, 1), min(last + context, len(lines))
        return "\n".join(f"{i:6d}\t{lines[i - 1]}" for i in range(lo, hi + 1))

    @staticmethod
    def _closest(content: str, old_string: str) -> str:
        """Point at the most similar region of the file, to make the next attempt cheaper."""
        want = old_string.splitlines()
        lines = content.splitlines()
        if not want or not lines:
            return ""
        n = len(want)
        best, best_i = 0.0, 0
        key = want[0].strip()
        for i in range(len(lines)):
            if key and difflib.SequenceMatcher(None, key, lines[i].strip()).quick_ratio() < 0.6:
                continue
            r = difflib.SequenceMatcher(None, "\n".join(want), "\n".join(lines[i:i + n])).ratio()
            if r > best:
                best, best_i = r, i
        if best < 0.5:
            return ""
        lo, hi = best_i + 1, min(best_i + n, len(lines))
        region = "\n".join(f"{j:6d}\t{lines[j - 1]}" for j in range(lo, hi + 1))
        return f" The closest match is lines {lo}-{hi}:\n{region}"

    TEST_DIRS = ("tests", "test", "__tests__", "spec")
    TEST_FILE = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$)")
    IMPORT_LINE = re.compile(r"^\s*(import\s.+\sfrom\s+['\"]\.|from\s+\.\S*\s+import\s|from\s+\w[\w.]*\s+import\s|import\s+\w[\w.]*\s*$)")

    def _test_import_hint(self, full: str) -> str:
        """When the model creates a new source file, show how nearby tests import code.

        Hidden tests are written like the existing ones, so their import style (default vs
        named exports, module paths) is the best evidence of how a new module will be used.
        """
        if self.TEST_FILE.search(os.path.basename(full)):
            return ""
        examples, seen = [], set()
        d = os.path.dirname(full)
        for _ in range(3):  # the file's directory and up to two parents, staying inside the repo
            if not d.startswith(self.workdir):
                break
            candidates = [os.path.join(d, t) for t in self.TEST_DIRS if os.path.isdir(os.path.join(d, t))] + [d]
            for tdir in candidates:
                for name in sorted(os.listdir(tdir))[:40]:
                    if not self.TEST_FILE.search(name):
                        continue
                    try:
                        with open(os.path.join(tdir, name), encoding="utf-8", errors="replace") as f:
                            head = f.read(4000).splitlines()
                    except OSError:
                        continue
                    for line in head:
                        if self.IMPORT_LINE.match(line) and line.strip() not in seen:
                            seen.add(line.strip())
                            rel = os.path.relpath(os.path.join(tdir, name), self.workdir)
                            examples.append(f"  {rel}: {line.strip()}")
                    if len(examples) >= 8:
                        break
                if len(examples) >= 8:
                    break
            if examples:
                break
            d = os.path.dirname(d)
        if not examples:
            return ""
        return ("\nHow existing tests near this file import code (hidden tests are written the same way, "
                "so make the new module importable like this):\n" + "\n".join(examples[:8]))

    DEFAULT_IMPORT = re.compile(r"^\s*import\s+\w+\s+from\s+['\"]\.")

    def _missing_default_exports(self):
        """New JS/TS source files without `export default`, where nearby tests use default imports."""
        r = subprocess.run("git add -N . && git diff --name-only --diff-filter=A", shell=True, cwd=self.workdir,
                           capture_output=True, text=True)
        subprocess.run(["git", "reset", "-q"], cwd=self.workdir, capture_output=True)
        missing = []
        for rel in r.stdout.split():
            if not re.search(r"\.(ts|tsx|js|jsx|mjs)$", rel) or self.TEST_FILE.search(os.path.basename(rel)) \
                    or rel.endswith(".d.ts"):
                continue
            full = os.path.join(self.workdir, rel)
            try:
                with open(full, encoding="utf-8", errors="replace") as f:
                    if "export default" in f.read():
                        continue
            except OSError:
                continue
            hint = self._test_import_hint(full)
            defaults = [line.strip() for line in hint.splitlines()
                        if self.DEFAULT_IMPORT.match(line.split(": ", 1)[-1])]
            if defaults:
                missing.append((rel, defaults[0]))
        return missing

    @staticmethod
    def _syntax_warning(full: str, content: str) -> str:
        if not full.endswith(".py"):
            return ""
        try:
            ast.parse(content)
        except SyntaxError as e:
            return (f"\nWARNING: the file no longer parses as Python: {e.msg} (line {e.lineno}). "
                    "The edit was applied; fix it.")
        return ""

    def _repo_changed(self) -> bool:
        return bool(self._diff_stat())

    def _diff_stat(self) -> str:
        r = subprocess.run("git add -N . && git diff --stat", shell=True, cwd=self.workdir,
                           capture_output=True, text=True)
        subprocess.run(["git", "reset", "-q"], cwd=self.workdir, capture_output=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    def done(self) -> str:
        """Accept done, but push back once on an empty diff and once on failing repro checks."""
        stat = self._diff_stat()
        if not stat and not self._empty_done_warned:
            self._empty_done_warned = True
            raise ToolError("the repository has no changes, so nothing would be submitted. Make the fix with "
                            "edit_file (scratch files in /tmp do not count). If you are certain no change is "
                            "needed, call done again.")
        failing = []
        for i, (desc, cmd) in enumerate(self.repro_checks, 1):
            code, out = self._run(cmd)
            if code != 0:
                failing.append(f"#{i} {desc}\n$ {cmd}\n{'TIMED OUT' if code is None else f'exit {code}'}\n"
                               f"{out.strip()[-1500:]}")
        if failing and not self._failing_done_warned:
            self._failing_done_warned = True
            raise ToolError("these reproduction checks still fail:\n\n" + "\n\n".join(failing) +
                            "\n\nFix the code (or the check, if the check itself is wrong), then call done again.")
        missing = [] if self._export_warned else self._missing_default_exports()
        if missing:
            self._export_warned = True
            raise ToolError(
                "the existing tests next to your new module(s) import modules with a default import, e.g.\n" +
                "\n".join(f"  {ex}" for _, ex in missing[:3]) +
                "\nbut these new files have no default export: " + ", ".join(f for f, _ in missing) +
                ". Hidden tests will import your module the same way, so also add a default export of the "
                "module's main function/class (keep the named exports), then call done again.")
        # The review costs at least one more turn; skip it once the loop has said the budget is nearly spent.
        if self.task_text and not self._reviewed and not self.budget_low:
            self._reviewed = True
            self._calls_since_review = 0
            checks = "\n".join(f"  #{i} {d}" for i, (d, _) in enumerate(self.repro_checks, 1)) or "  (none)"
            raise ToolError(
                "final review before finishing. Here is the task again:\n\n" + self.task_text.strip()[:6000] +
                "\n\nChanged files:\n" + (stat or "(none)") +
                "\n\nYour registered repro checks:\n" + checks +
                "\n\nSplit the task into its individual requirements (every behaviour, case, name, signature, "
                "data shape and message it states, including each \"also\"/\"and\"/\"must\"). For each one, "
                "find the repro check that verifies it; if none does, write one and register it with "
                "repro_check now. Also list every assumption you made that the task does not state "
                "(for example an input data format taken from your knowledge of an external API rather than "
                "the task's own words, whether a new function is a default or a named export, where a new "
                "value is passed): where the task states it, follow the task literally; where the task is "
                "silent, make the code work either way (e.g. export both ways). Then make sure your change "
                "satisfies all of them (every named "
                "function/class/file/parameter exists exactly as stated, new parameters are passed along "
                "everywhere, the code builds and its nearby tests pass), fix anything missing, and call done again.")
        # A review that is acknowledged without checking anything is no review: push back once.
        if self._reviewed and self._calls_since_review == 0 and not self._review_skip_warned \
                and not self.budget_low:
            self._review_skip_warned = True
            raise ToolError("you called done again without checking anything since the final review. Go through "
                            "the task's requirements one by one, register a repro_check for each one not yet "
                            "covered, run them, and only then call done.")
        summary = "Task marked as done."
        if self.repro_checks:
            summary += f"\nReproduction checks: {len(self.repro_checks) - len(failing)}/{len(self.repro_checks)} pass."
        return summary + (f"\nChanged files:\n{stat}" if stat else "")
