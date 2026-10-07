"""Validate madsLoop_logs/*.jsonl against the handout's schema (section 0.3).

usage: python3 scripts/check_logs.py [LOG_FILE_OR_DIR ...]   (default: madsLoop_logs/)
"""
import glob
import json
import os
import sys
from datetime import datetime

REQUIRED = {
    "run_start": {"model_id": str, "workdir": str},
    "api_request": {"iteration": int, "prompt_tokens": int, "completion_tokens": int, "total_tokens": int},
    "api_retry": {"iteration": int, "error": str, "backoff_s": (int, float)},
    "tool_call": {"iteration": int, "tool_name": str, "arguments": dict},
    "tool_result": {"iteration": int, "tool_name": str, "result": str, "is_error": bool},
    "run_end": {"reason": str, "num_iterations": int},
}
# done / no_tool_calls / error, plus our self-imposed limits (MAX_ITERATIONS, MAX_WALL_S in agentic_loop.py)
RUN_END_REASONS = {"done", "no_tool_calls", "error", "max_iterations", "time_limit"}


def check_file(path):
    errors = []
    events = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            try:
                rec = json.loads(line)
            except ValueError as e:
                errors.append(f"line {n}: not JSON ({e})")
                continue
            ev = rec.get("event")
            if ev not in REQUIRED:
                errors.append(f"line {n}: unknown event {ev!r}")
                continue
            try:
                datetime.fromisoformat(rec.get("timestamp", ""))
            except (TypeError, ValueError):
                errors.append(f"line {n}: bad timestamp {rec.get('timestamp')!r}")
            want = REQUIRED[ev]
            extra = set(rec) - set(want) - {"timestamp", "event"}
            if extra:
                errors.append(f"line {n} ({ev}): extra fields {sorted(extra)}")
            for field, typ in want.items():
                if field not in rec:
                    errors.append(f"line {n} ({ev}): missing {field}")
                elif not isinstance(rec[field], typ) or (typ is int and isinstance(rec[field], bool)):
                    errors.append(f"line {n} ({ev}): {field} has type {type(rec[field]).__name__}")
            events.append((n, ev, rec))

    if not events or events[0][1] != "run_start":
        errors.append("first event is not run_start")
    if not events or events[-1][1] != "run_end":
        errors.append("last event is not run_end")
    if events and events[-1][1] == "run_end" and events[-1][2].get("reason") not in RUN_END_REASONS:
        errors.append(f"run_end.reason {events[-1][2].get('reason')!r} not in {sorted(RUN_END_REASONS)}")
    if sum(ev == "run_start" for _, ev, _ in events) != 1 or sum(ev == "run_end" for _, ev, _ in events) != 1:
        errors.append("expected exactly one run_start and one run_end")
    # every tool_call is immediately answered by a tool_result for the same tool
    for i, (n, ev, rec) in enumerate(events):
        if ev == "tool_call":
            nxt = events[i + 1] if i + 1 < len(events) else None
            if not nxt or nxt[1] != "tool_result" or nxt[2]["tool_name"] != rec["tool_name"]:
                errors.append(f"line {n}: tool_call not followed by its tool_result")
    iters = [rec["iteration"] for _, ev, rec in events if ev == "api_request"]
    if iters != list(range(1, len(iters) + 1)):
        errors.append(f"api_request iterations not 1..N: {iters[:10]}...")
    if events and events[-1][1] == "run_end" and iters and events[-1][2]["num_iterations"] < iters[-1]:
        errors.append("run_end.num_iterations is less than the last api_request iteration")
    return errors, len(events)


def main(args):
    paths = []
    for a in args or ["madsLoop_logs"]:
        paths += sorted(glob.glob(os.path.join(a, "**", "*.jsonl"), recursive=True)) if os.path.isdir(a) else [a]
    if not paths:
        sys.exit("no log files found")
    bad = 0
    for p in paths:
        errors, n = check_file(p)
        print(f"{'OK  ' if not errors else 'FAIL'} {p} ({n} events)")
        for e in errors[:20]:
            print(f"     {e}")
        bad += bool(errors)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main(sys.argv[1:])
