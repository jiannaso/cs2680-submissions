"""The tools the model can call. Every tool works on the task repository through the Sandbox."""

import base64
import difflib
import json
import re
import shlex

from . import config
from .sandbox import SandboxError, SandboxPathError


# git subcommands that move HEAD, stage, or throw work away: the patch is `git diff` of the working
# tree against the starting commit, so any of these can silently empty or corrupt it.
BLOCKED_GIT = re.compile(
    r"(?:^|[;&|(`]|\$\(|\b(?:then|do|else|sudo|xargs|env|time)\s)\s*git\s+(?:-{1,2}[\w-]+(?:[= ](?!-)\S+)?\s+)*"
    r"(add|commit|stash|reset|checkout|switch|clean|merge|rebase|cherry-pick|revert|am|pull|push|worktree)\b"
)
# view_file's line-number prefix ("    42\t"), sometimes copied into old_str by the model
LINE_NO_PREFIX = re.compile(r"^ *\d+\t", re.M)

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command (sh -c) in the repository root and return stdout+stderr and "
                "the exit code. No internet. No interactive commands. Long output is truncated, so "
                "narrow it with grep, head, tail or sed -n. git commands that change repository "
                "state (add, commit, stash, reset, checkout, ...) are refused: leave your fix as "
                "uncommitted changes."),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command to run."},
                    "timeout_s": {"type": "integer",
                                  "description": f"Timeout in seconds (default {config.CMD_TIMEOUT_S}, "
                                                 f"max {config.CMD_TIMEOUT_MAX_S})."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_file",
            "description": (
                f"Show a file with line numbers, at most {config.VIEW_MAX_LINES} lines per call. "
                "Use start_line/end_line for large files."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repository root."},
                    "start_line": {"type": "integer", "description": "First line (1-based)."},
                    "end_line": {"type": "integer", "description": "Last line (inclusive)."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": (
                "Replace one exact occurrence of old_str with new_str in a file. old_str must match "
                "the file exactly (including whitespace and indentation) and occur exactly once; "
                "include enough surrounding lines to make it unique."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a file, or overwrite it completely, with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": (
                "Call this once the fix is complete and verified. Your changes in the repository "
                "are then submitted for grading."),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "One or two sentences on what you changed."},
                },
                "required": ["summary"],
            },
        },
    },
]


def truncate(text: str, limit: int = None) -> str:
    """Keep the head and tail of a long output, with a note about what was cut."""
    limit = limit or config.TOOL_OUTPUT_LIMIT
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    cut = len(text) - head - tail
    return f"{text[:head]}\n\n[... {cut} characters cut ...]\n\n{text[-tail:]}"


class ToolBox:
    """Runs one tool call on one task's sandbox and returns (result_text, is_error)."""

    def __init__(self, sandbox, alias_root: str = None):
        self.sb = sandbox
        # a candidate working in a copy of the repository: paths under the main checkout
        # (alias_root, e.g. /app) are redirected to the copy, so it never edits another candidate's files
        self.alias_root = alias_root if alias_root and alias_root != sandbox.workdir else None

    def _remap_path(self, path: str) -> str:
        root = self.alias_root
        if path == root or path.startswith(root + "/"):
            return self.sb.workdir + path[len(root):]
        return path

    def _remap_command(self, command: str) -> str:
        return re.sub(r"(?<![\w./-])" + re.escape(self.alias_root) + r"(?=/|[^\w.-]|$)",
                      lambda _: self.sb.workdir, command)

    def run_raw(self, name: str, args: dict):
        """run() with the arguments as a dict (for the harness's own edits)."""
        return self.run(name, json.dumps(args))

    def run(self, name: str, raw_args: str):
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            return f"Error: could not parse the arguments as JSON ({e}).", True
        if self.alias_root:
            if isinstance(args.get("path"), str):
                args["path"] = self._remap_path(args["path"])
            if isinstance(args.get("command"), str):
                args["command"] = self._remap_command(args["command"])
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return f"Error: unknown tool {name!r}.", True
        try:
            return handler(**args)
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}", True
        except SandboxPathError as e:
            return f"Error: {e}", True
        except SandboxError as e:
            return f"Error: the sandbox failed: {e}", True

    # -- the tools --------------------------------------------------------------------------

    def _tool_bash(self, command: str, timeout_s: int = None):
        blocked = BLOCKED_GIT.search(command)
        if blocked:
            return (f"Error: `git {blocked.group(1)}` is not allowed: leave the fix as uncommitted changes in the "
                    "working tree. Use read-only git (status, diff, log, show) or edit files directly."), True
        timeout_s = int(timeout_s or config.CMD_TIMEOUT_S)
        timeout_s = max(1, min(timeout_s, config.CMD_TIMEOUT_MAX_S))
        r = self.sb.exec(command, timeout_s=timeout_s)
        out = truncate(r.get("output", ""))
        note = f"[exit code {r.get('exit_code')}]"
        if r.get("timed_out"):
            note = f"[timed out after {timeout_s}s]"
        return f"{out}\n{note}" if out else note, bool(r.get("timed_out"))

    def _read(self, path: str) -> str:
        """read_text, but files outside the repository (e.g. /tmp) are read with cat."""
        try:
            return self.sb.read_text(path)
        except SandboxPathError:
            if not path.startswith("/"):
                raise
            r = self.sb.exec(f"cat -- {shlex.quote(path)}", timeout_s=30)
            if r.get("exit_code"):
                raise SandboxPathError(r.get("output", "").strip() or f"cannot read {path}")
            return r.get("output", "")

    def _write(self, path: str, content: str) -> bool:
        """write_text, but files outside the repository (e.g. /tmp) are written through the shell."""
        try:
            return self.sb.write_text(path, content)
        except SandboxPathError:
            if not path.startswith("/"):
                raise
            q = shlex.quote(path)
            b64 = base64.b64encode(content.encode()).decode()
            r = self.sb.exec(f"existed=0; [ -e {q} ] && existed=1; mkdir -p \"$(dirname {q})\" && "
                             f"printf %s {b64} | base64 -d > {q} && echo $existed", timeout_s=60)
            if r.get("exit_code"):
                raise SandboxPathError(r.get("output", "").strip() or f"cannot write {path}")
            return r.get("output", "").strip().endswith("1")

    def _tool_view_file(self, path: str, start_line: int = None, end_line: int = None):
        text = self._read(path)
        lines = text.splitlines()
        start = max(1, int(start_line or 1))
        end = int(end_line or start + config.VIEW_MAX_LINES - 1)
        end = min(end, len(lines), start + config.VIEW_MAX_LINES - 1)
        if start > len(lines):
            return f"{path} has only {len(lines)} lines.", True
        body = "\n".join(f"{i:6d}\t{lines[i - 1]}" for i in range(start, end + 1))
        more = f"\n[lines {start}-{end} of {len(lines)}]" if end < len(lines) or start > 1 else ""
        return truncate(body) + more, False

    def _tool_str_replace(self, path: str, old_str: str, new_str: str):
        text = self._read(path)
        if not old_str:
            return "Error: old_str is empty.", True
        if "\r\n" in text:                              # keep a CRLF file's line endings as they are
            old_str = old_str.replace("\r\n", "\n").replace("\n", "\r\n")
            new_str = new_str.replace("\r\n", "\n").replace("\n", "\r\n")
        count = text.count(old_str)
        note = ""
        if count == 0 and LINE_NO_PREFIX.search(old_str):
            # the model copied view_file's line numbers: strip them from both sides and retry
            stripped = LINE_NO_PREFIX.sub("", old_str)
            if text.count(stripped) == 1:
                old_str, new_str = stripped, LINE_NO_PREFIX.sub("", new_str)
                count, note = 1, " (removed view_file line-number prefixes from old_str/new_str)"
        if count == 0:
            return (f"Error: old_str was not found in {path}. Copy the text exactly, including indentation, "
                    f"without line-number prefixes.{_closest(text, old_str)}"), True
        if count > 1:
            return f"Error: old_str occurs {count} times in {path}; include more context.", True
        new_text = text.replace(old_str, new_str, 1)
        self._write(path, new_text)
        line = text[:text.index(old_str)].count("\n") + 1
        return f"Edited {path}{note}. The changed region now reads:\n{_snippet(new_text, line, new_str)}", False

    def _tool_create_file(self, path: str, content: str):
        existed = self._write(path, content)
        return f"{'Overwrote' if existed else 'Created'} {path}.", False

    def _tool_finish(self, summary: str = ""):
        return "Submitting your changes.", False


def _snippet(content: str, first: int, new_str: str, context: int = 3) -> str:
    """The edited region with a few lines of context, so the model can check the result."""
    lines = content.splitlines()
    last = first + new_str.count("\n")
    lo, hi = max(first - context, 1), min(last + context, len(lines))
    if hi - lo > 60:                                    # a big edit: its first and last lines are enough
        keep = list(range(lo, lo + 25)) + list(range(hi - 24, hi + 1))
        out, prev = [], None
        for i in keep:
            if prev is not None and i != prev + 1:
                out.append("   ...")
            out.append(f"{i:6d}\t{lines[i - 1]}")
            prev = i
        return "\n".join(out)
    return "\n".join(f"{i:6d}\t{lines[i - 1]}" for i in range(lo, hi + 1))


def _closest(content: str, old_str: str) -> str:
    """The region of the file most like old_str, so the next try needs no extra view_file."""
    want = old_str.splitlines()
    lines = content.splitlines()
    if not want or not lines or len(lines) > 20_000:
        return ""
    n = len(want)
    best, best_i = 0.0, 0
    keys = [w.strip() for w in want if w.strip()][:1]
    for i in range(len(lines)):
        if keys and difflib.SequenceMatcher(None, keys[0], lines[i].strip()).quick_ratio() < 0.6:
            continue
        r = difflib.SequenceMatcher(None, "\n".join(want), "\n".join(lines[i:i + n])).ratio()
        if r > best:
            best, best_i = r, i
    if best < 0.5:
        return " The text does not resemble any part of the file: view the region you want to change first."
    lo, hi = best_i + 1, min(best_i + n + 2, len(lines))
    region = "\n".join(f"{j:6d}\t{lines[j - 1]}" for j in range(lo, hi + 1))
    return f" The closest match is lines {lo}-{hi} (copy from here, without the line numbers):\n{truncate(region, 6000)}"


# Test files the hidden tests may replace: our edits to them are dropped before the patch is made,
# so that the graders' test patch applies cleanly.
TEST_PATH_GREP = r"(^|/)(tests?|__tests__|testing)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$"


def clean_repo_for_patch(sb, start_sha: str = None):
    """Undo edits to test files and remove scratch/build files before the diff is taken. If HEAD
    moved (a commit from inside a script the git filter cannot see), point it back at the starting
    commit, keeping the working tree; and unstage everything."""
    pattern = shlex.quote(TEST_PATH_GREP)
    restore = (f"[ \"$(git rev-parse HEAD 2>/dev/null)\" = {shlex.quote(start_sha)} ] || "
               f"git reset -q --soft {shlex.quote(start_sha)} ; " if start_sha else "")
    script = (
        restore + "git reset -q >/dev/null 2>&1 ; " +
        # restore modified tracked test files
        f"git diff --name-only | grep -E {pattern} | xargs -r git checkout -- ; "
        # delete untracked test files, scratch scripts and large/binary files
        f"git ls-files --others --exclude-standard | grep -E {pattern} | xargs -r rm -f ; "
        "git ls-files --others --exclude-standard | grep -E '(^|/)(repro|reproduce|scratch|debug)[^/]*$' "
        "| xargs -r rm -f ; "
        "git ls-files --others --exclude-standard -z | xargs -0 -r -I{} sh -c "
        "'[ $(wc -c < \"{}\") -gt 200000 ] && rm -f \"{}\"' ; "
        "git status --short | head -50"
    )
    try:
        return sb.exec(script, timeout_s=60).get("output", "")
    except Exception as e:  # never let cleanup stop the submission
        return f"cleanup failed: {e}"


# -- build/lint gate: run by the harness (not the model) before a patch is graded ------------------

# Reports names a changed .py file uses but never defines, imports or binds anywhere, and that the
# file at HEAD did not already use undefined (so dynamic tricks that were there before are ignored).
# Conservative on purpose: any binding of a name anywhere in the file counts as a definition.
PY_UNDEFINED_CHECK = r'''
import ast, builtins, subprocess, sys
EXTRA = {"__file__", "__name__", "__doc__", "__builtins__", "__spec__", "__loader__", "__package__",
         "__path__", "__annotations__", "__class__", "__qualname__", "__module__", "__dict__", "__debug__"}
def undefined(src):
    tree = ast.parse(src)
    defined = set(dir(builtins)) | EXTRA
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names):
            return {}
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            defined.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
            defined.add(n.id)
        elif isinstance(n, ast.arg):
            defined.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            defined.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            defined.update(n.names)
        elif type(n).__name__ in ("MatchAs", "MatchStar", "TypeVar", "ParamSpec", "TypeVarTuple") and getattr(n, "name", None):
            defined.add(n.name)
        elif type(n).__name__ == "MatchMapping" and getattr(n, "rest", None):
            defined.add(n.rest)
    used = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in defined:
            used.setdefault(n.id, n.lineno)
    return used
bad = 0
for path in sys.argv[1:]:
    try:
        new = open(path, encoding="utf-8", errors="replace").read()
        now = undefined(new)
    except SyntaxError as e:
        print("%s:%s: syntax error: %s" % (path, e.lineno, e.msg)); bad = 1; continue
    except Exception:
        continue
    try:
        old = subprocess.run(["git", "show", "HEAD:" + path], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL).stdout.decode("utf-8", "replace")
        before = undefined(old) if old else {}
    except Exception:
        before = {}
    for name, line in sorted(now.items(), key=lambda x: x[1]):
        if name not in before:
            print("%s:%s: undefined name '%s' (not defined, imported or assigned anywhere in the file)" % (path, line, name)); bad = 1
sys.exit(bad)
'''


def _changed_files(sb) -> list:
    """Paths (relative to the repository root) of modified and new files, deleted ones left out."""
    r = sb.exec("git add -N . >/dev/null 2>&1; git diff --name-only --diff-filter=d", timeout_s=60)
    return [l.strip() for l in r.get("output", "").splitlines() if l.strip()]


def check_build(sb) -> str:
    """Cheap compile/lint checks on the files the patch changes. Returns the problems found as
    text for the model, or "" when everything checked is fine (or nothing could be checked)."""
    try:
        files = _changed_files(sb)
    except Exception:
        return ""
    problems = []
    py = [f for f in files if f.endswith(".py")]
    if py:
        b64 = base64.b64encode(PY_UNDEFINED_CHECK.encode()).decode()
        script = (f"PY=$(command -v python3 || command -v python) || exit 0; "
                  f"printf %s {b64} | base64 -d > /tmp/.a3_pycheck.py && "
                  f"\"$PY\" /tmp/.a3_pycheck.py {' '.join(shlex.quote(f) for f in py)}")
        try:
            r = sb.exec(script, timeout_s=120)
            if r.get("exit_code") and r.get("output", "").strip():
                problems.append("Python check (syntax and undefined names in the files you changed):\n"
                                + truncate(r["output"].strip(), 3000))
        except Exception:
            pass
    go_dirs = sorted({"./" + (f.rsplit("/", 1)[0] if "/" in f else ".")
                      for f in files if f.endswith(".go")})
    if go_dirs:
        # compiles each changed package, but not its _test.go files: when the specification changes
        # a signature, the existing tests still call the old one and the hidden tests replace them
        cmd = (f"go build {' '.join(shlex.quote(d) for d in go_dirs)} > /tmp/.a3_go.txt 2>&1; "
               f"rc=$?; tail -60 /tmp/.a3_go.txt; exit $rc")
        try:
            r = sb.exec(cmd, timeout_s=config.BUILD_GATE_TIMEOUT_S)
            out = r.get("output", "").strip()
            if r.get("timed_out"):
                pass                                    # too slow to tell: do not block on it
            elif r.get("exit_code") and out:
                problems.append(f"Go build of the changed packages ({' '.join(go_dirs)}) failed:\n"
                                + truncate(out, 3000))
        except Exception:
            pass
    return "\n\n".join(problems)


# -- parallel candidates: extra copies of the repository under /work --------------------------------

def make_candidate_dir(sb, name: str, root: str = "/work"):
    """A git worktree of the task repository at <root>/<name>, at HEAD, with the repository's
    ignored top-level entries (node_modules, build outputs, ...) linked in so tests still run.
    Returns its path, or None when it cannot be made."""
    dest = f"{root.rstrip('/')}/{name}"
    q = shlex.quote(dest)
    script = (
        f"rm -rf {q}; git worktree prune >/dev/null 2>&1; "
        f"git worktree add --detach {q} HEAD >/dev/null 2>&1 || exit 3; "
        "exclude=\"$(git rev-parse --git-common-dir)/info/exclude\"; mkdir -p \"$(dirname \"$exclude\")\"; "
        "git ls-files --others --ignored --exclude-standard --directory | sed 's#/$##' | grep -v / | "
        "while IFS= read -r p; do "
        f"  [ -e {q}/\"$p\" ] || ln -s \"$PWD/$p\" {q}/\"$p\"; "
        "  grep -qxF \"/$p\" \"$exclude\" 2>/dev/null || echo \"/$p\" >> \"$exclude\"; "
        "done; "
        f"cd {q} && git status --porcelain | head -5; echo DONE"
    )
    try:
        r = sb.exec(script, timeout_s=300)
    except Exception:
        return None
    out = r.get("output", "")
    lines = [l for l in out.splitlines() if l.strip()]
    if r.get("exit_code") or not lines or lines[-1] != "DONE" or len(lines) > 1:
        return None                                     # failed, or the copy is not clean
    return dest


def repo_diff(sb) -> str:
    """The patch of the repository at sb.workdir, as extract_patch makes it."""
    try:
        r = sb.exec("git add -N . >/dev/null 2>&1; git diff 2>/dev/null", timeout_s=120)
    except Exception:
        return ""
    return r.get("output", "") if not r.get("exit_code") else ""


# -- reconnaissance: the searches every candidate starts with, done once by the harness ---------------

_RECON_STOP = {
    "None", "True", "False", "self", "cls", "this", "string", "error", "errors", "return", "returns",
    "func", "def", "class", "type", "types", "interface", "bool", "boolean", "int", "str", "dict", "list",
    "float", "bytes", "object", "context", "Context", "nil", "null", "undefined", "void", "async",
    "await", "const", "static", "public", "private", "method", "function", "struct", "Input", "Output",
    "Description", "Path", "Name", "Type", "value", "values", "data", "name", "path", "file", "test",
    "tests", "true", "false", "string[]", "Promise", "Optional", "Any", "Union", "Exception",
}
_PATH_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,5})\b")
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")


def spec_names(interface, *texts, limit: int = 12):
    """Code-like identifiers and repository paths a specification mentions, the interface's own names
    first, then by frequency."""
    interface = interface or ""
    text = "\n".join([interface] + [t or "" for t in texts])
    paths = list(dict.fromkeys(re.findall(r"Path\s*:\s*`?([\w./-]+\.[A-Za-z0-9]{1,5})\b", interface)
                               + _PATH_RE.findall(text)))
    declared = []                                       # names the interface declares outright
    for m in re.findall(r"(?:Name|Function|Method|Class|Type|Struct|Interface)\s*:\s*`?([\w.]+)", interface):
        declared += [i for i in m.split(".") if len(i) >= 3 and i not in _RECON_STOP]
    counts = {}
    for ident in _IDENT_RE.findall(text):
        code_like = ("_" in ident.strip("_") or re.search(r"[a-z][A-Z]", ident)
                     or (ident[0].isupper() and re.search(r"[a-z]", ident) and ident in interface and len(ident) >= 6))
        noise = (re.search(r"\d_\d", ident)                                  # x86_64, el8_9
                 or not re.search(r"(?<!Description: )(?<!Description:)\b" + re.escape(ident) + r"\b", text))
        if code_like and not noise and len(ident) >= 4 and ident not in _RECON_STOP and not ident.isupper():
            counts[ident] = counts.get(ident, 0) + 1
    names = list(dict.fromkeys(declared))
    names += [n for n in sorted(counts, key=lambda n: (-counts[n], text.find(n))) if n not in names]
    return names[:limit], paths[:10]


def recon(sb, task: dict) -> str:
    """A short map of where the specification's names live in the repository, the test files next to
    them and how to run tests: the searches every candidate would otherwise spend its first turns on."""
    return recon_full(sb, task)[0]


def recon_full(sb, task: dict):
    """recon()'s text, plus what it found as data: {"names", "sources" (ranked non-test files to
    read), "missing" (paths the interface names that do not exist yet), "tests"}."""
    names, paths = spec_names(task.get("interface"), task.get("requirements"), task.get("problem_statement"))
    info = {"names": names, "sources": [], "missing": [], "tests": []}
    if not names and not paths:
        return "", info
    script = []
    for p in paths:
        script.append(f"echo '@@path {p}'; git ls-files --error-unmatch -- {shlex.quote(p)} >/dev/null 2>&1 "
                      f"&& echo exists || echo missing")
    for n in names:
        script.append(f"echo '@@name {n}'; git grep -I -c -w -F -e {shlex.quote(n)} 2>/dev/null "
                      "| grep -v -E '(^|/)(vendor|node_modules|third_party|dist|build)/' "
                      "| grep -v -E '\\.(md|rst|txt|json|lock|pg_dump|sql|csv|ya?ml|html|svg|po|pot|snap)?:[0-9]+$' "
                      "| sort -t: -k2 -nr | head -5")
    script.append("echo '@@lang'; for f in go.mod package.json pyproject.toml setup.cfg pytest.ini tox.ini setup.py; do "
                  "[ -f $f ] && echo $f; done; [ -f package.json ] && grep -E '\"test[^\"]*\"\\s*:' package.json | head -3")
    try:
        r = sb.exec("; ".join(script), timeout_s=120)
    except Exception:
        return "", info
    sections, cur = {}, None
    for line in r.get("output", "").splitlines():
        if line.startswith("@@"):
            cur = line[2:]
            sections[cur] = []
        elif cur is not None and line.strip():
            sections[cur].append(line.strip())

    out, files, spread = [], {}, {}
    for p in paths:
        state = (sections.get(f"path {p}") or ["?"])[0]
        out.append(f"- {p}: {'exists' if state == 'exists' else 'does not exist yet (to create)'}")
    for n in names:
        hits = []
        for line in sections.get(f"name {n}", []):
            f, _, c = line.rpartition(":")
            if f and c.isdigit():
                hits.append(f"{f} ({c})")
                files[f] = files.get(f, 0) + int(c)
                spread[f] = spread.get(f, 0) + 1                # distinct names the file mentions
        out.append(f"- {n}: {', '.join(hits) if hits else 'not found in the repository (new code)'}")
    test_re = re.compile(TEST_PATH_GREP)
    # test files next to the source files that hold the specific names (those found in few files)
    specific_src = []
    for n in names:
        found = [l.rpartition(":")[0] for l in sections.get(f"name {n}", []) if l.rpartition(":")[2].isdigit()]
        if 0 < len(found) <= 3:
            specific_src += [f for f in found if not test_re.search(f)]
    dirs = list(dict.fromkeys(f.rsplit("/", 1)[0] if "/" in f else "." for f in specific_src))[:4]
    near = []
    if dirs:
        try:
            r = sb.exec("git ls-files -- " + " ".join(shlex.quote(d + "/") for d in dirs)
                        + " | grep -E " + shlex.quote(TEST_PATH_GREP) + " | head -40", timeout_s=60)
            listed = [f for f in r.get("output", "").split()
                      if f.rsplit("/", 1)[-1] not in ("__init__.py", "conftest.py")
                      and re.search(r"\.(py|go|[jt]sx?|java|rb|rs)$", f)]
            stems = {f.rsplit("/", 1)[-1].split(".")[0] for f in specific_src}
            named = [f for f in listed if any(st and st in f.rsplit("/", 1)[-1] for st in stems)]
            near = named[:4]                            # test_load_book.py for load_book.py, ...
            for d in dirs:                              # then the nearest others
                near += [f for f in listed if f.rsplit("/", 1)[0] == d or f.startswith(d + "/")][:2]
        except Exception:
            pass
    ranked = [f for f in sorted(files, key=lambda f: (-spread[f], -files[f])) if test_re.search(f)]
    tests = list(dict.fromkeys(near + ranked))[:6]
    existing = [p for p in paths if (sections.get(f"path {p}") or ["?"])[0] == "exists"]
    info["missing"] = [p for p in paths if p not in existing]
    by_spread = [f for f in sorted(files, key=lambda f: (-spread[f], -files[f])) if not test_re.search(f)
                 and not re.search(r"(\.pb\.go|\.gen\.go|_generated\.\w+|\.min\.js)$|(^|/)examples?/", f)]
    info["sources"] = list(dict.fromkeys(existing + specific_src + by_spread))[:8]
    info["tests"] = tests
    lang = sections.get("lang", [])
    hints = []
    if "go.mod" in lang:
        hints.append("Go: `go build ./<pkg>/` and `go test ./<pkg>/ -run <TestName>`")
    if any(x in lang for x in ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "setup.py")):
        hints.append("Python: `python -m pytest <file>::<test> -q -x`")
    if "package.json" in lang:
        scripts = [l for l in lang if l.startswith('"test')]
        hints.append("JS/TS: `npx jest <path>`" + (f" (package.json: {'; '.join(scripts)})" if scripts else ""))
    text = ("# Repository map (collected by the harness before you started: start from these locations "
            "instead of repeating these searches)\n"
            "Where the names in the specification occur (file and number of matching lines):\n" + "\n".join(out))
    if tests:
        text += "\nExisting test files that mention them:\n" + "\n".join(f"- {t}" for t in tests)
    if hints:
        text += "\nRunning tests: " + "; ".join(hints)
    return truncate(text, 4000), info
