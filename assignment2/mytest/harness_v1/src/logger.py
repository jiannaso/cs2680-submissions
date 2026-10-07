"""JSONL trace logger following the madsLoop_logs/ schema (handout 0.3)."""
import json
import os
from datetime import datetime, timezone


class RunLogger:
    def __init__(self, enabled: bool, log_dir: str = "madsLoop_logs"):
        self.enabled = enabled
        self._fh = None
        if enabled:
            os.makedirs(log_dir, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            path = os.path.join(log_dir, f"run_{stamp}_{os.getpid()}.jsonl")
            self._fh = open(path, "a", encoding="utf-8")

    def event(self, event: str, **fields):
        if not self.enabled:
            return
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            **fields,
        }
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self):
        if self._fh:
            self._fh.close()
