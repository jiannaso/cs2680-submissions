"""madsLoop: a minimal coding-agent harness.

usage: python madsLoop.py -p "<problem statement>" [--log] <workdir>
"""
import argparse
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# openai is installed here by docker_env.sh, away from the repo's own Python environment.
if os.path.isdir("/tmp/madsloop_deps"):
    sys.path.insert(1, "/tmp/madsloop_deps")

from src.agentic_loop import run_agent  # noqa: E402
from src.logger import RunLogger  # noqa: E402

MODEL_ID = "qwen3.6-35b-a3b"


def main():
    parser = argparse.ArgumentParser(description="madsLoop coding agent")
    parser.add_argument("-p", "--problem", required=True, help="issue text")
    parser.add_argument("--log", action="store_true", help="write trace to ./madsLoop_logs/")
    parser.add_argument("workdir", help="path to the repo checkout")
    args = parser.parse_args()

    workdir = os.path.abspath(args.workdir)
    if not os.path.isdir(workdir):
        parser.error(f"workdir does not exist: {workdir}")

    # Turn SIGTERM (docker stop, timeouts) into SystemExit so cleanup and run_end still happen.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))

    logger = RunLogger(enabled=args.log)
    reason = run_agent(args.problem, workdir, MODEL_ID, logger)
    print(f"madsLoop finished: {reason}", file=sys.stderr)
    # Always exit 0: whatever the agent managed to edit should still be diffed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
