"""Context growth: replace old tool outputs with short stubs once the prompt gets large.

Compaction happens in one batch when the prompt passes COMPACT_AT_TOKENS, not a
little every turn: the proxy caches the longest unchanged prefix of the
conversation, so rewriting old messages on every turn would forfeit the cache.
The system prompt, the task, and the model's own messages are never touched.
"""
import json

COMPACT_AT_TOKENS = 80_000  # the model's window is 262k; quality drops well before that
KEEP_RECENT = 8  # most recent tool results kept in full
MIN_CHARS = 600  # results shorter than this are not worth stubbing
HEAD_CHARS = 200  # how much of an elided result to keep


def _describe_calls(messages):
    """tool_call_id -> short human-readable description of the call."""
    calls = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            fn = tc["function"]
            try:
                args = json.loads(fn["arguments"] or "{}")
            except ValueError:
                args = {}
            detail = args.get("command") or args.get("path") or ""
            calls[tc["id"]] = f"{fn['name']} {detail}".strip()[:150]
    return calls


def compact(messages, keep_recent=KEEP_RECENT):
    """Stub out all but the last `keep_recent` tool results in place. Returns chars removed."""
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    old = tool_idx[:-keep_recent] if keep_recent else tool_idx
    calls = _describe_calls(messages)
    saved = 0
    for i in old:
        content = messages[i]["content"]
        if len(content) < MIN_CHARS:
            continue
        stub = (f"{content[:HEAD_CHARS]}\n[... older output of `{calls.get(messages[i]['tool_call_id'], 'tool')}` "
                f"elided to save context ({len(content)} chars). Run it again if you need it.]")
        saved += len(content) - len(stub)
        messages[i] = {**messages[i], "content": stub}
    return saved
