"""One-shot candidate: the harness gathers the relevant code, one strong-model call writes the whole fix
as SEARCH/REPLACE blocks, the harness applies them and runs the build gate, with one repair call if a
block does not apply or the build fails. A few model calls instead of a 60-turn exploration, so an
easy task can be solved and graded within minutes while the agent candidates keep working."""

import re

from . import config
from .tools import check_build, clean_repo_for_patch, truncate

BLOCK_RE = re.compile(
    r"^[ \t]*`*(?P<path>[^\n`]*?)`*[ \t]*\n(?:```[\w+-]*\n)?<<<<<<< SEARCH\n(?P<search>.*?)^=======[ \t]*\n"
    r"(?P<replace>.*?)^>>>>>>> REPLACE[ \t]*$",
    re.S | re.M)


def parse_blocks(text: str) -> list:
    """[(path, search, replace)] from the model's answer; an empty SEARCH creates or replaces a file."""
    blocks = []
    for m in BLOCK_RE.finditer(text or ""):
        path = m.group("path").strip().strip("`").strip()
        path = re.sub(r"^(?:File|Path|file|path)\s*:\s*", "", path).strip()
        if path:
            blocks.append((path, m.group("search"), m.group("replace")))
    return blocks


def apply_blocks(tools, blocks: list):
    """Apply blocks in order through the candidate's ToolBox. Returns (applied, errors)."""
    applied, errors = 0, []
    for path, search, replace in blocks:
        if not search.strip():
            result, is_error = tools.run_raw("create_file", {"path": path, "content": replace})
        else:
            result, is_error = tools.run_raw("str_replace", {"path": path, "old_str": search, "new_str": replace})
        if is_error:
            errors.append(f"Block for {path}: {result}")
        else:
            applied += 1
    return applied, errors


def _windows(text: str, names: list, around: int = 50) -> str:
    """The parts of a big file around lines that mention the specification's names."""
    lines = text.splitlines()
    hits = [i for i, l in enumerate(lines) if any(n in l for n in names)]
    if not hits:
        return "\n".join(lines[:200]) + ("\n... (rest of the file omitted)" if len(lines) > 200 else "")
    spans = []
    for i in hits:
        lo, hi = max(0, i - around), min(len(lines), i + around + 1)
        if spans and lo <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], hi)
        else:
            spans.append([lo, hi])
    parts = []
    for lo, hi in spans:
        if lo > 0:
            parts.append(f"... (lines 1-{lo} omitted)" if not parts else "... (lines omitted)")
        parts.append("\n".join(lines[lo:hi]))
    if spans[-1][1] < len(lines):
        parts.append("... (rest of the file omitted)")
    return "\n".join(parts)


def gather_context(sb, info: dict) -> str:
    """The source files the reconnaissance ranked highest (whole when small, else the parts around the
    specification's names), and the closest existing test file, within ONESHOT_CONTEXT_CHARS."""
    budget = config.ONESHOT_CONTEXT_CHARS
    out = []
    files = info.get("sources", [])[:config.ONESHOT_MAX_FILES] + info.get("tests", [])[:1]
    for f in files:
        try:
            text = sb.read_text(f)
        except Exception:
            continue
        body = text if len(text) <= config.ONESHOT_WHOLE_FILE_CHARS else _windows(text, info.get("names", []))
        body = truncate(body, max(2000, min(len(body), budget)))
        out.append(f"## {f}\n```\n{body}\n```")
        budget -= len(body)
        if budget <= 2000:
            break
    for p in info.get("missing", []):
        out.append(f"## {p}\n(does not exist yet)")
    return "\n\n".join(out)
