"""Copy Claude Code session transcripts into developer_logs/ with secrets redacted.

usage: python3 scripts/export_dev_logs.py SESSION_ID [SESSION_ID ...]

Transcripts live in ~/.claude/projects/<project>/<session_id>.jsonl, where
<project> is this directory's absolute path with "/" replaced by "-". Rerun
after each work session; files are overwritten with the latest version.
"""
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The transcript folder is named after the directory the session was started in; override it with
# CLAUDE_PROJECT_DIR if the repo has been moved or renamed since (e.g. a2/ -> assignment2/).
PROJECT_DIR = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.expanduser("~/.claude/projects/" + REPO.replace("/", "-"))
OUT_DIR = os.path.join(REPO, "developer_logs")

SECRET_PATTERNS = [
    re.compile(r"hyi-[A-Za-z0-9_\-]{16,}"),            # CS2680 proxy keys
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),             # OpenAI / Anthropic style keys
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),         # GitHub tokens
]


def redact(text: str) -> tuple[str, int]:
    count = 0
    live_key = os.environ.get("CS2680_API_KEY")
    if live_key and len(live_key) >= 8:
        count += text.count(live_key)
        text = text.replace(live_key, "[REDACTED_API_KEY]")
    for pat in SECRET_PATTERNS:
        text, n = pat.subn("[REDACTED_API_KEY]", text)
        count += n
    return text, count


def main(session_ids):
    if not session_ids:
        sys.exit(__doc__)
    os.makedirs(OUT_DIR, exist_ok=True)
    for sid in session_ids:
        src = os.path.join(PROJECT_DIR, f"{sid}.jsonl")
        with open(src, encoding="utf-8") as f:
            text, n = redact(f.read())
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip():
                json.loads(line)  # redaction must leave every line valid JSON
        dst = os.path.join(OUT_DIR, f"claude_code_session_{sid[:8]}.jsonl")
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"{sid} -> {os.path.relpath(dst, REPO)} ({len(text.splitlines())} lines, {n} secrets redacted)")


if __name__ == "__main__":
    main(sys.argv[1:])
