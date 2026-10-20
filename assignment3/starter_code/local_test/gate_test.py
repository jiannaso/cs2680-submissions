#!/usr/bin/env python3
"""Tests for the build/lint gate, the no-edit nudge and the identical-patch skip, with a real
sandbox_server.py on a tiny git repo, a fake dispatcher and a scripted model (no API, no Docker).

    python3 local_test/gate_test.py

check     check_build() flags a new undefined name and a syntax error, ignores names that were
          already undefined at HEAD
gate      the model's first patch uses an undefined name; the gate hands it the error, the model
          fixes it, and only the fixed patch is graded
nudge     the model explores for many turns; at NO_EDIT_NUDGE_AT it is told to edit and does
same      the retry ends with the patch that already failed: it is not graded a second time
Go packages are not covered here (no go toolchain needed): try check_build on a Go task image.
"""
import json, os, subprocess, sys, tempfile, threading, time, types
STARTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.dont_write_bytecode = True
sys.path.insert(0, STARTER)
os.environ.update(CS2680_BASE_URL="http://unused", CS2680_API_KEY="x",
                  CS2680_MODEL_STANDARD="standard", CS2680_MODEL_EXPERT="expert", CS2680_MODEL_STARTER="starter")
from dispatcher import DispatcherError
from evaluation_scripts.trace_logger import TraceLogger
import src.config as config
config.RETRY_MIN_RUN_LEFT_S = 0
config.PLAYBOOK_ENABLED = False
config.NUM_WORKERS = 1
config.API_BACKOFF_S = 0.01
from src import agentic_loop
from src.sandbox import Sandbox
from src.tools import check_build

work = os.path.realpath(tempfile.mkdtemp())
CALC = "import os\n\ndef helper(x):\n    return x\n\ndef add(a, b):\n    return a - b\n\ndef legacy():\n    return OLD_GLOBAL\n"


def make_repo(name, port):
    repo = os.path.join(work, name); os.makedirs(repo)
    open(os.path.join(repo, "calc.py"), "w").write(CALC)
    subprocess.run(f"cd {repo} && git init -q && git add -A && git -c user.email=a@b -c user.name=t commit -qm init",
                   shell=True, check=True)
    wrapper = (f"import sys,runpy; sys.argv=['s','--root','{repo}','--port','{port}']; "
               f"g=runpy.run_path('{STARTER}/src/sandbox_server.py', run_name='lib'); "
               f"g['main'].__globals__['WORK_ROOT']='{work}/w'; sys.exit(g['main']())")
    proc = subprocess.Popen([sys.executable, "-c", wrapper], stderr=subprocess.DEVNULL)
    return repo, proc, f"http://127.0.0.1:{port}"


class Logger(TraceLogger):
    def __init__(self): super().__init__(False)
    def _emit(self, ev, f): pass


class FakeDispatcher:
    def __init__(self, task, passes):
        self.task, self.passes, self.evals, self.final, self.given = task, passes, [], None, False
    def next_task(self):
        if self.given: return None
        self.given = True; return self.task
    def logger(self, k): return Logger()
    def patch_path(self, k): return os.path.join(work, f"patch_{self.task['workdir'].rsplit('/', 1)[1]}.diff")
    def extract_patch(self, k):
        d = subprocess.run("git add -N . && git diff", shell=True, cwd=self.task["workdir"],
                           capture_output=True, text=True).stdout
        open(self.patch_path(k), "w").write(d); return len(d)
    def submit_patch(self, k, text): open(self.patch_path(k), "w").write(text); return len(text)
    def evaluate(self, k):
        p = open(self.patch_path(k)).read()
        if not p: raise DispatcherError("no patch")
        ok = self.passes(p)
        ev = {"tests_failed": 0 if ok else 1, "tests_total": 2, "attempt": len(self.evals) + 1}
        self.evals.append((ev, p)); return ev
    def continue_task(self, k):
        if os.path.exists(self.patch_path(k)): os.remove(self.patch_path(k))
    def done(self, k, reason="done", iterations=None):
        self.final = (reason, self.evals[-1][0] if self.evals else None); return {}


def tc(i, name, args):
    return types.SimpleNamespace(id=f"c{i}", type="function",
                                 function=types.SimpleNamespace(name=name, arguments=json.dumps(args)))


def reply(*calls):
    msg = types.SimpleNamespace(content="", tool_calls=list(calls) or None)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)],
                                 usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))


def run_agent(task, passes, script):
    d = FakeDispatcher(task, passes)
    agent = agentic_loop.Agent(d)
    agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=lambda model=None, messages=None, **kw: script(messages))))
    agent.run()
    return d


def last_user(messages):
    return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")


results = {}
procs = []

# -- check -------------------------------------------------------------------------------------
repo, proc, url = make_repo("check", 18810); procs.append(proc); time.sleep(1.5)
sb = Sandbox(url, repo)
results["check: clean repo"] = check_build(sb) == ""
open(os.path.join(repo, "calc.py"), "w").write(CALC.replace("return a - b", "return key_int(a) + b"))
out = check_build(sb)
results["check: new undefined name"] = "key_int" in out and "OLD_GLOBAL" not in out
open(os.path.join(repo, "calc.py"), "w").write(CALC.replace("return a - b", "return (a + b"))
results["check: syntax error"] = "syntax error" in check_build(sb)
open(os.path.join(repo, "calc.py"), "w").write(CALC.replace("return a - b", "return helper(a) + b"))
results["check: fixed"] = check_build(sb) == ""

# -- gate --------------------------------------------------------------------------------------
repo, proc, url = make_repo("gate", 18811); procs.append(proc); time.sleep(1.5)
seen = {"gate_prompt": False}
def gate_model(messages):
    if "automatic check" in last_user(messages) and not seen["gate_prompt"]:
        seen["gate_prompt"] = True
        return reply(tc(2, "str_replace", {"path": "calc.py", "old_str": "key_int(a)", "new_str": "helper(a)"}),
                     tc(3, "finish", {"summary": "fixed the name"}))
    if seen["gate_prompt"]:
        return reply(tc(9, "finish", {"summary": "done"}))
    return reply(tc(1, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return key_int(a) + b"}),
                 tc(4, "finish", {"summary": "fixed"}))
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "", "interface": "",
               "sandbox_url": url, "workdir": repo}, lambda p: "helper(a) + b" in p and "key_int" not in p, gate_model)
results["gate: model saw the error"] = seen["gate_prompt"]
results["gate: only the fixed patch was graded"] = (len(d.evals) == 1 and d.evals[0][0]["tests_failed"] == 0)

# -- nudge -------------------------------------------------------------------------------------
repo, proc, url = make_repo("nudge", 18812); procs.append(proc); time.sleep(1.5)
nudge = {"turn": None, "n": 0}
def nudge_model(messages):
    nudge["n"] += 1
    if "still has no changes" in last_user(messages) and nudge["turn"] is None:
        nudge["turn"] = nudge["n"]
        return reply(tc(2, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return a + b"}),
                     tc(3, "finish", {"summary": "fixed"}))
    return reply(tc(1, "bash", {"command": "cat calc.py"}))
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "", "interface": "",
               "sandbox_url": url, "workdir": repo}, lambda p: "a + b" in p, nudge_model)
results["nudge: sent at the first threshold"] = nudge["turn"] == config.NO_EDIT_NUDGE_AT[0] + 1
results["nudge: task solved after it"] = d.final and d.final[1] and d.final[1]["tests_failed"] == 0

# -- same --------------------------------------------------------------------------------------
repo, proc, url = make_repo("same", 18813); procs.append(proc); time.sleep(1.5)
def same_model(messages):
    if "Your patch was graded" in last_user(messages):
        return reply(tc(5, "bash", {"command": "cat calc.py"}), tc(6, "finish", {"summary": "looks right"}))
    if any(m["role"] == "tool" for m in messages):
        return reply(tc(9, "finish", {"summary": "done"}))
    return reply(tc(1, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return a * b"}))
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "", "interface": "",
               "sandbox_url": url, "workdir": repo}, lambda p: "a + b" in p, same_model)
results["same: graded once"] = len(d.evals) == 1
results["same: last evaluation kept"] = d.final and d.final[1] == d.evals[0][0]
results["same: no patch left to grade at done()"] = not os.path.exists(d.patch_path(0))

for p in procs: p.terminate()
for name, ok in results.items():
    print(("ok   " if ok else "FAIL ") + name)
print("PASS" if all(results.values()) else "FAIL")
sys.exit(0 if all(results.values()) else 1)
