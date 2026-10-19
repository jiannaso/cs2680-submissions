#!/usr/bin/env python3
"""Plumbing test, no API key and no Docker needed: your real Agent against real sandbox_server.py
processes on tiny git repos, a fake dispatcher and a scripted model.

    python3 local_test/fake_run.py [fix|retry|flaky|notools|badjson|all]

fix      the model edits, finishes, the patch passes
retry    the first patch fails grading, the retry fixes it
flaky    every third API call raises (the agent must retry)
notools  the model never calls a tool (the agent must give up cleanly)
badjson  the first tool call has invalid JSON arguments
compact  big tool outputs force context compaction mid-task
playbook tasks run one at a time; later tasks must get the expert's playbook
Checks to read in the output: each task's final result, that the patch only touches calc.py
(scratch files and test-file edits are cleaned up), and that the trace has every event type.
"""
import json, os, subprocess, sys, tempfile, threading, time, types
STARTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCEN = sys.argv[1] if len(sys.argv) > 1 else "fix"
if SCEN == "all":
    bad = 0
    for s in ("fix", "retry", "flaky", "notools", "badjson", "compact", "playbook"):
        print(f"===== {s}", flush=True)
        bad |= subprocess.run([sys.executable, os.path.abspath(__file__), s]).returncode
    sys.exit(bad)
sys.dont_write_bytecode = True
sys.path.insert(0, STARTER)
os.environ.update(CS2680_BASE_URL="http://unused", CS2680_API_KEY="x",
                  CS2680_MODEL_STANDARD="standard", CS2680_MODEL_EXPERT="expert", CS2680_MODEL_STARTER="starter")
from dispatcher import DispatcherError
from evaluation_scripts.trace_logger import TraceLogger
import src.config as config
config.RETRY_MIN_RUN_LEFT_S = 0
config.PLAYBOOK_MIN_RUN_LEFT_S = 0
if SCEN == "compact":
    config.COMPACT_AT_CHARS = 12_000
    config.COMPACT_KEEP_MESSAGES = 4
if SCEN == "playbook":
    config.NUM_WORKERS = 1                         # sequential, so later tasks see the playbook

N_TASKS = 3
work = os.path.realpath(tempfile.mkdtemp())
servers, tasks = [], []
for k in range(N_TASKS):
    repo = os.path.join(work, f"repo{k}"); os.makedirs(os.path.join(repo, "tests"))
    open(os.path.join(repo, "calc.py"), "w").write("def add(a, b):\n    return a - b\n")
    open(os.path.join(repo, "tests", "test_calc.py"), "w").write("from calc import add\n")
    subprocess.run(f"cd {repo} && git init -q && git add -A && git -c user.email=a@b -c user.name=t commit -qm init", shell=True, check=True)
    port = 18700 + k
    wrapper = f"import sys,runpy; sys.argv=['s','--root','{repo}','--port','{port}']; import os; g=runpy.run_path('{STARTER}/src/sandbox_server.py', run_name='lib'); g['WORK_ROOT']='{work}/w'; g['main'].__globals__['WORK_ROOT']='{work}/w'; sys.exit(g['main']())"
    servers.append(subprocess.Popen([sys.executable, "-c", wrapper], stderr=subprocess.DEVNULL))
    tasks.append({"task": k, "problem_statement": "add() subtracts", "requirements": "add returns a+b",
                  "interface": "add(a, b)", "sandbox_url": f"http://127.0.0.1:{port}", "workdir": repo})
time.sleep(1.5)

class Logger(TraceLogger):
    def __init__(self): super().__init__(False); self.events = []
    def _emit(self, ev, f): self.events.append((ev, f))

class FakeDispatcher:
    def __init__(self):
        self.lock = threading.Lock(); self.queue = list(tasks); self.live = set(); self.loggers = {}
        self.patches = {}; self.evals = {k: [] for k in range(N_TASKS)}; self.finals = {}; self.max_live = 0
    def next_task(self):
        with self.lock:
            if len(self.live) >= 5: raise DispatcherError("5 live")
            if not self.queue: return None
            t = self.queue.pop(0); self.live.add(t["task"]); self.max_live = max(self.max_live, len(self.live)); return t
    def logger(self, k): return self.loggers.setdefault(k, Logger())
    def patch_path(self, k): return os.path.join(work, f"patch{k}.diff")
    def extract_patch(self, k):
        d = subprocess.run("git add -N . && git diff", shell=True, cwd=tasks[k]["workdir"], capture_output=True, text=True).stdout
        open(self.patch_path(k), "w").write(d); return len(d)
    def submit_patch(self, k, text): open(self.patch_path(k), "w").write(text); return len(text)
    def evaluate(self, k):
        p = open(self.patch_path(k)).read()
        if not p: raise DispatcherError("no patch")
        ok = "return a + b" in p
        ev = {"tests_failed": 0 if ok else 2, "tests_total": 3, "attempt": len(self.evals[k]) + 1}
        self.evals[k].append((ev, p)); return ev
    def continue_task(self, k): os.remove(self.patch_path(k))
    def done(self, k, reason="done", iterations=None):
        with self.lock: self.live.discard(k)
        last = self.evals[k][-1][0] if self.evals[k] else None
        self.finals[k] = (reason, iterations, last); return {}

# scripted model: per task, a list of tool-call turns keyed off the conversation
def tc(i, name, args):
    return types.SimpleNamespace(id=f"c{i}", type="function", function=types.SimpleNamespace(name=name, arguments=json.dumps(args)))
def reply(calls, content=""):
    msg = types.SimpleNamespace(content=content, tool_calls=calls or None)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=10, total_tokens=110))

STEP = {}
SYSTEMS = {}        # task -> system prompt it was given (to check the playbook reaches later tasks)
STATS = {"compact": 0, "playbook": 0}

def script(messages):
    wd = messages[0]["content"].split("checked out in the current directory at ")[1].split(". There")[0]
    k = int(wd.rsplit("repo", 1)[1])
    SYSTEMS.setdefault(k, messages[0]["content"])
    nevals = len(d.evals[k])
    retry = nevals > 0
    key = (k, nevals)
    n = STEP.get(key, 0); STEP[key] = n + 1
    if retry:
        n += 2                                   # a retry goes straight to the edit
    bad = SCEN == "retry" and not retry
    big = "x" * 3000 if SCEN == "compact" else ""
    steps = [
        [tc(1, "bash", {"command": f"grep -n def -r . --include=*.py; echo {big}"})],
        [tc(2, "view_file", {"path": "calc.py"})],
        [tc(3, "str_replace", {"path": "calc.py", "old_str": "return a - b" if not retry else "return a * b",
                               "new_str": "return a * b" if bad else "return a + b"}),
         tc(4, "bash", {"command": "echo 'x' > /tmp/repro_fake.py; echo 'x' > repro.py; echo junk >> tests/test_calc.py; echo hi"})],
        [tc(5, "create_file", {"path": "/tmp/scratch_fake.txt", "content": "hello"}),
         tc(6, "view_file", {"path": "/tmp/scratch_fake.txt"})],
        [tc(7, "finish", {"summary": "fixed"})],
    ]
    if SCEN == "compact":                         # pad the middle with reads so compaction triggers
        steps = steps[:2] + [[tc(10 + i, "bash", {"command": f"echo {big}"})] for i in range(6)] + steps[2:]
        if retry: n += 6
    if SCEN == "notools": return reply(None, "I think it's fine")
    if SCEN == "badjson" and n == 0:
        return reply([types.SimpleNamespace(id="z", type="function", function=types.SimpleNamespace(name="bash", arguments="{not json"))])
    if SCEN == "badjson": n -= 1
    return reply(steps[min(n, len(steps) - 1)])

class FakeCompletions:
    calls = 0
    def create(self, model, messages, **kw):
        FakeCompletions.calls += 1
        if SCEN == "flaky" and FakeCompletions.calls % 3 == 1: raise TimeoutError("proxy hiccup")
        json.dumps(messages); time.sleep(0.2)
        if not kw.get("tools"):                   # compaction or playbook request
            if "playbook" in messages[0]["content"]:
                assert model == "expert", model
                STATS["playbook"] += 1
                return reply(None, "- PLAYBOOK-LESSON: grep for every name in the interface first.")
            STATS["compact"] += 1
            return reply(None, "SUMMARY: calc.py has add(); it subtracts. Nothing edited yet.")
        return script(messages)

from src import agentic_loop
config.API_BACKOFF_S = 0.01
d = FakeDispatcher()
agent = agentic_loop.Agent(d)
agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))
agent.run()
for k in range(N_TASKS):
    status = subprocess.run("git status --short", shell=True, cwd=tasks[k]["workdir"], capture_output=True, text=True).stdout.strip()
    evs = [e for e, _ in d.evals[k]]
    print(f"task {k}: final={d.finals.get(k)} evals={evs} repo_status={status!r}")
    print("   patch files:", [l for l in (d.evals[k][-1][1] if d.evals[k] else "").splitlines() if l.startswith("diff --git")])
    print("   trace events:", sorted({e for e, _ in d.loggers[k].events}), len(d.loggers[k].events))
print("max live:", d.max_live, "model calls:", FakeCompletions.calls, "stats:", STATS)
print("playbook in system prompt:", {k: "PLAYBOOK-LESSON" in v for k, v in sorted(SYSTEMS.items())})
for s in servers: s.terminate()
expect_solved = SCEN != "notools"
ok = all(((d.finals[k][2] or {}).get("tests_failed") == 0) == expect_solved for k in range(N_TASKS))
ok = ok and all(not d.evals[k] or [l for l in d.evals[k][-1][1].splitlines() if l.startswith("diff --git")] == ["diff --git a/calc.py b/calc.py"] for k in range(N_TASKS))
if SCEN == "compact":
    ok = ok and STATS["compact"] >= N_TASKS
if SCEN == "playbook":
    ok = ok and [("PLAYBOOK-LESSON" in SYSTEMS[k]) for k in range(N_TASKS)] == [False, True, True]
if SCEN != "notools":
    ok = ok and STATS["playbook"] >= 1
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
