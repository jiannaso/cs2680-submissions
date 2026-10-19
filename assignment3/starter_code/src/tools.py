"""The tools the model can call. Every tool works on the task repository through the Sandbox."""

import json
import shlex

from . import config
from .sandbox import SandboxError, SandboxPathError


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command (sh -c) in the repository root and return stdout+stderr and "
                "the exit code. No internet. No interactive commands. Long output is truncated, so "
                "narrow it with grep, head, tail or sed -n."),
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

    def __init__(self, sandbox):
        self.sb = sandbox

    def run(self, name: str, raw_args: str):
        try:
            args = json.loads(raw_args or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            return f"Error: could not parse the arguments as JSON ({e}).", True
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
        timeout_s = int(timeout_s or config.CMD_TIMEOUT_S)
        timeout_s = max(1, min(timeout_s, config.CMD_TIMEOUT_MAX_S))
        r = self.sb.exec(command, timeout_s=timeout_s)
        out = truncate(r.get("output", ""))
        note = f"[exit code {r.get('exit_code')}]"
        if r.get("timed_out"):
            note = f"[timed out after {timeout_s}s]"
        return f"{out}\n{note}" if out else note, bool(r.get("timed_out"))

    def _tool_view_file(self, path: str, start_line: int = None, end_line: int = None):
        text = self.sb.read_text(path)
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
        text = self.sb.read_text(path)
        count = text.count(old_str)
        if not old_str:
            return "Error: old_str is empty.", True
        if count == 0:
            return (f"Error: old_str was not found in {path}. View the file again and copy the text "
                    "exactly, including indentation."), True
        if count > 1:
            return f"Error: old_str occurs {count} times in {path}; include more context.", True
        self.sb.write_text(path, text.replace(old_str, new_str, 1))
        line = text[:text.index(old_str)].count("\n") + 1
        return f"Edited {path} (replacement starts at line {line}).", False

    def _tool_create_file(self, path: str, content: str):
        existed = self.sb.write_text(path, content)
        return f"{'Overwrote' if existed else 'Created'} {path}.", False

    def _tool_finish(self, summary: str = ""):
        return "Submitting your changes.", False


# Test files the hidden tests may replace: our edits to them are dropped before the patch is made,
# so that the graders' test patch applies cleanly.
TEST_PATH_GREP = r"(^|/)(tests?|__tests__|testing)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$"


def clean_repo_for_patch(sb):
    """Undo edits to test files and remove scratch/build files before the diff is taken."""
    pattern = shlex.quote(TEST_PATH_GREP)
    script = (
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
