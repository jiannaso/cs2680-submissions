#!/usr/bin/env python3
"""Run your real Agent with the real course model on a task's real repository, without Docker.

    export CS2680_API_KEY=...
    python3 local_test/run_local.py --tasks 0            # one task from agent_task_input.json
    python3 local_test/run_local.py --tasks 0,1 --fail-first

What is real: src/ (your Agent, tools, prompts), the course API (through dispatcher/egress_proxy.py,
as in run_all.py), src/sandbox_server.py serving the task's repository at its base commit.
What is not: the repository runs on this machine, not in the task image, so its own tests usually
cannot run (missing dependencies), and there is no grading (the hidden tests are not available):
evaluate() reports every patch as passing, or with --fail-first as failing once to exercise the
retry path. Judge the patch yourself against the task statement.

Output: local_test/out/<k>/run.jsonl (the trace), local_test/out/<k>/patch.diff, and a summary with
turns, tokens and the estimated cost per task.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                         # starter_code/
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True

OUT = os.path.join(HERE, "out")
REPOS = os.path.expanduser(os.environ.get("A3_LOCAL_REPOS", "~/.cache/cs2680_a3_local/repos"))
UPSTREAM = "https://api.cs2680.com"
# $ per 1M tokens (input, output), from the assignment; cached input is ignored here.
PRICES = {"expert": (2.00, 10.00), "standard": (0.08, 0.28), "starter": (0.01, 0.04)}


def log(msg):
    print(f"[local] {msg}", file=sys.stderr, flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url, timeout=30):
    import urllib.request
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception as e:
            if hasattr(e, "code"):                   # an HTTP error still means it is up
                return True
            time.sleep(0.3)
    return False


def checkout(entry) -> str:
    """Clone the task's repository (blobs on demand) and check out its base commit, clean."""
    repo = entry["repo"]
    path = os.path.join(REPOS, repo.replace("/", "__"))
    if not os.path.isdir(os.path.join(path, ".git")):
        os.makedirs(REPOS, exist_ok=True)
        log(f"cloning {repo} (first time only) ...")
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", "-q",
                        f"https://github.com/{repo}.git", path], check=True)
    subprocess.run(["git", "-C", path, "checkout", "-q", "-f", entry["base_commit"]], check=True)
    subprocess.run(["git", "-C", path, "clean", "-q", "-fdx"], check=True)
    return os.path.realpath(path)


def start_sandbox(repo_path, work_root):
    port = free_port()
    os.makedirs(work_root, exist_ok=True)
    # sandbox_server.py creates /work, which macOS does not allow: point WORK_ROOT elsewhere.
    code = ("import runpy, sys; "
            f"sys.argv = ['sandbox_server.py', '--root', {repo_path!r}, '--port', '{port}']; "
            f"g = runpy.run_path({os.path.join(ROOT, 'src', 'sandbox_server.py')!r}, run_name='lib'); "
            f"g['main'].__globals__['WORK_ROOT'] = {work_root!r}; sys.exit(g['main']())")
    # The agent's commands run on this machine: keep pip off the network and out of your Python
    # environments, as in the offline task containers.
    env = dict(os.environ, PIP_NO_INDEX="1", PIP_REQUIRE_VIRTUALENV="1", PIP_CONFIG_FILE=os.devnull)
    env.pop("CS2680_API_KEY", None)
    proc = subprocess.Popen([sys.executable, "-c", code], stderr=subprocess.DEVNULL, env=env)
    url = f"http://127.0.0.1:{port}"
    if not wait_http(url + "/health"):
        proc.kill()
        sys.exit(f"sandbox server for {repo_path} did not start")
    return proc, url


def start_proxy(session):
    port = free_port()
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "dispatcher", "egress_proxy.py"),
                             "--port", str(port), "--upstream", UPSTREAM, "--session", session],
                            stderr=subprocess.DEVNULL)
    if not wait_http(f"http://127.0.0.1:{port}/v1/models"):
        proc.kill()
        sys.exit("egress proxy did not start")
    return proc, f"http://127.0.0.1:{port}/v1"


class LocalDispatcher:
    """The dispatcher calls the Agent makes, served in-process."""

    def __init__(self, tasks, fail_first):
        from dispatcher.dispatcher import TaskTraceLogger
        self._TaskTraceLogger = TaskTraceLogger
        self._lock = threading.Lock()
        self._queue = list(tasks)
        self._tasks = {t["task"]: t for t in tasks}
        self._live = set()
        self._loggers = {}
        self.fail_first = fail_first
        self.results = {}                            # k -> dict for the summary

    def next_task(self):
        from dispatcher import DispatcherError
        with self._lock:
            if len(self._live) >= 5:
                raise DispatcherError("5 tasks are already live")
            if not self._queue:
                return None
            t = self._queue.pop(0)
            self._live.add(t["task"])
            self.results[t["task"]] = {"attempts": 0, "started": time.time()}
        self.logger(t["task"]).run_start(t["workdir"])
        log(f"task {t['task']} opened")
        return dict(t)

    def patch_path(self, k):
        d = os.path.join(OUT, str(k))
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "patch.diff")

    def submit_patch(self, k, text):
        with open(self.patch_path(k), "w") as f:
            f.write(text)
        return len(text)

    def extract_patch(self, k):
        wd = self._tasks[k]["workdir"]
        subprocess.run("git add -N . ", shell=True, cwd=wd, capture_output=True)
        diff = subprocess.run(["git", "diff"], cwd=wd, capture_output=True, text=True).stdout
        return self.submit_patch(k, diff)

    def evaluate(self, k):
        from dispatcher import DispatcherError
        if not os.path.exists(self.patch_path(k)) or not os.path.getsize(self.patch_path(k)):
            raise DispatcherError("no patch to evaluate")
        r = self.results[k]
        r["attempts"] += 1
        failed = 1 if (self.fail_first and r["attempts"] == 1) else 0
        ev = {"tests_failed": failed, "tests_total": 1, "attempt": r["attempts"]}
        log(f"task {k} attempt {r['attempts']}: pretend grading -> {failed}/1 failing")
        return ev

    def continue_task(self, k):
        log(f"task {k}: continuing")
        try:
            os.remove(self.patch_path(k))
        except OSError:
            pass

    def done(self, k, reason="done", iterations=None):
        with self._lock:
            self._live.discard(k)
        self.logger(k).run_end(reason, int(iterations or 0))
        self.logger(k).close()
        r = self.results[k]
        r.update(reason=reason, iterations=iterations, seconds=time.time() - r["started"])
        log(f"task {k} done ({reason})")
        return {}

    def logger(self, k):
        if k not in self._loggers:
            self._loggers[k] = self._TaskTraceLogger(os.path.join(OUT, str(k), "run.jsonl"), True)
        return self._loggers[k]


def summarize(k, result):
    trace = os.path.join(OUT, str(k), "run.jsonl")
    calls, tools, errors, cost = 0, {}, 0, 0.0
    tok_in = tok_out = 0
    with open(trace) as f:
        for line in f:
            e = json.loads(line)
            if e["event"] == "api_request":
                calls += 1
                pi, po = e.get("prompt_tokens") or 0, e.get("completion_tokens") or 0
                tok_in, tok_out = tok_in + pi, tok_out + po
                pin, pout = PRICES.get(e.get("model_id"), (0, 0))
                cost += pi / 1e6 * pin + po / 1e6 * pout
            elif e["event"] == "tool_call":
                tools[e["tool_name"]] = tools.get(e["tool_name"], 0) + 1
            elif e["event"] == "tool_result" and e.get("is_error"):
                errors += 1
    patch = os.path.join(OUT, str(k), "patch.diff")
    files = []
    if os.path.exists(patch):
        files = [l.split(" b/", 1)[1].strip() for l in open(patch) if l.startswith("diff --git")]
    print(f"\n== task {k}: {result.get('reason')}  ({result.get('seconds', 0):.0f}s, "
          f"{result.get('attempts')} grading(s))")
    print(f"   model calls {calls}, tokens in {tok_in:,} / out {tok_out:,}, est. cost ${cost:.3f} "
          "(no cache discount)")
    print(f"   tool calls {sum(tools.values())} {tools}, tool errors {errors}")
    print(f"   patch files: {files or 'none'}")
    print(f"   trace: {trace}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default="0", help="comma-separated task indices (default 0)")
    ap.add_argument("--input", default=os.path.join(ROOT, "evaluation_scripts", "agent_task_input.json"))
    ap.add_argument("--fail-first", action="store_true", help="report the first grading as failing")
    ap.add_argument("--workers", type=int, help="override config.NUM_WORKERS (1 = tasks in order, so "
                    "later tasks get the playbook written after earlier ones)")
    args = ap.parse_args()

    if not os.environ.get("CS2680_API_KEY"):
        sys.exit("export CS2680_API_KEY first")
    for tier in ("EXPERT", "STANDARD", "STARTER"):
        os.environ.setdefault(f"CS2680_MODEL_{tier}", tier.lower())

    entries = json.load(open(args.input))
    ks = [int(x) for x in args.tasks.split(",") if x.strip()]
    if os.path.isdir(OUT):
        for k in ks:
            shutil.rmtree(os.path.join(OUT, str(k)), ignore_errors=True)

    try:
        from dispatcher.session import session_id
        session = session_id()
    except BaseException:                            # no name saved and no terminal to ask
        session = f"local_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    procs = []
    try:
        proxy, base_url = start_proxy(session)
        procs.append(proxy)
        os.environ["CS2680_BASE_URL"] = base_url
        log(f"course API via {base_url} (session {session})")

        tasks = []
        for k in ks:
            e = entries[str(k)]
            path = checkout(e)
            sb, url = start_sandbox(path, os.path.join(REPOS, "_work", str(k)))
            procs.append(sb)
            tasks.append({"task": k, "problem_statement": e.get("problem_statement", ""),
                          "requirements": e.get("requirements", ""), "interface": e.get("interface", ""),
                          "sandbox_url": url, "workdir": path})
            log(f"task {k}: {e['repo']} @ {e['base_commit'][:10]} served at {url}")

        from src import config
        from src.agentic_loop import Agent
        if args.workers:
            config.NUM_WORKERS = args.workers
        d = LocalDispatcher(tasks, args.fail_first)
        agent = Agent(d)
        agent.run()
        for k in ks:
            if k in d.results:
                summarize(k, d.results[k])
        print(f"\n== playbook at the end of the run:\n{agent.playbook or '(empty)'}")
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
