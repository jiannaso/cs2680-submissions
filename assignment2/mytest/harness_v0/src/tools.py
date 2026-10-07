"""Tool schemas (what the model sees) and executors (what the harness runs)."""
import os
import subprocess

MAX_OUTPUT_CHARS = 12000
BASH_TIMEOUT_S = 300

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in the repository root and return stdout+stderr. "
                           f"Times out after {BASH_TIMEOUT_S}s. Use for grep/find/ls, running tests and scripts.",
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
            "description": "Read a file with line numbers. Optionally pass start_line/end_line (1-based, inclusive) for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "path relative to the repo root, or absolute"},
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
            "description": "Replace an exact string in a file. old_string must occur exactly once (include surrounding "
                           "lines to make it unique). With old_string empty, creates the file with new_string as content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
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
            "name": "done",
            "description": "Declare the task finished, after the fix is made and verified.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


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
    def __init__(self, workdir: str):
        self.workdir = os.path.realpath(workdir)

    def _resolve(self, path: str, allow_tmp: bool = False) -> str:
        full = os.path.realpath(os.path.join(self.workdir, path))
        if full == self.workdir or full.startswith(self.workdir + os.sep):
            return full
        if allow_tmp and full.startswith("/tmp" + os.sep):
            return full
        raise ToolError(f"path {path!r} is outside the repository {self.workdir}")

    def execute(self, name: str, args: dict) -> str:
        """Run one tool. Raises ToolError for problems the model should see as errors."""
        if name == "bash":
            return self.bash(args["command"])
        if name == "read_file":
            return self.read_file(args["path"], args.get("start_line"), args.get("end_line"))
        if name == "edit_file":
            return self.edit_file(args["path"], args["old_string"], args["new_string"])
        if name == "done":
            return "Task marked as done."
        raise ToolError(f"unknown tool {name!r}")

    def bash(self, command: str) -> str:
        try:
            proc = subprocess.run(
                command, shell=True, cwd=self.workdir, capture_output=True, text=True,
                timeout=BASH_TIMEOUT_S, errors="replace", stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"command timed out after {BASH_TIMEOUT_S}s")
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            out += f"\n[exit code {proc.returncode}]"
        return out or "[no output]"

    def read_file(self, path: str, start_line=None, end_line=None) -> str:
        full = self._resolve(path, allow_tmp=True)
        if not os.path.isfile(full):
            raise ToolError(f"no such file: {path}")
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        start = max(int(start_line or 1), 1)
        end = min(int(end_line or len(lines)), len(lines))
        body = "\n".join(f"{i:6d}\t{lines[i - 1]}" for i in range(start, end + 1))
        return f"{path} (lines {start}-{end} of {len(lines)})\n{body}"

    def edit_file(self, path: str, old_string: str, new_string: str) -> str:
        full = self._resolve(path, allow_tmp=True)
        if old_string == "":
            if os.path.exists(full):
                raise ToolError(f"{path} already exists; pass a non-empty old_string to edit it")
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(new_string)
            return f"created {path}"
        if not os.path.isfile(full):
            raise ToolError(f"no such file: {path}")
        with open(full, encoding="utf-8") as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            raise ToolError(f"old_string not found in {path}; read_file it again and copy the text exactly "
                            "(whitespace and indentation must match)")
        if count > 1:
            raise ToolError(f"old_string occurs {count} times in {path}; include more surrounding context")
        with open(full, "w", encoding="utf-8") as f:
            f.write(content.replace(old_string, new_string, 1))
        return f"edited {path}"
