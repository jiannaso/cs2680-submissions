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
review    the expert reviewer reports a gap; the model fixes it before the first grading
cands     two candidates in parallel: the copy is a git worktree with ignored node_modules linked in,
          paths under the main checkout are redirected to the copy, the main checkout is untouched
          by candidate 1, and grading (as each candidate finishes) stops at the first passing patch
dedup     two candidates with the same patch: it is graded once
early     candidate 0 passes quickly: candidate 1 is stopped instead of running to its turn limit
iface     interface parsing; a patch missing a name the interface declares is sent back before grading
api       at most MAX_CONCURRENT_CALLS model calls in flight; 429s are waited out without using retries
tools     str_replace shows the closest region on a miss, strips view_file line numbers, shows the
          edited region; state-changing git is refused; a commit made anyway is undone before the diff
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
config.CANDIDATES = 1                              # the cands/dedup scenarios turn it on
config.BUDGET_NOTE = True                          # off by default; the nudge scenario checks it
config.API_BACKOFF_S = 0.01
from src import agentic_loop
from src.sandbox import Sandbox
from src.tools import ToolBox, check_build, clean_repo_for_patch, interface_entries, recon, repo_diff

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


def reply(*calls, content=""):
    msg = types.SimpleNamespace(content=content, tool_calls=list(calls) or None)
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

# -- tools -------------------------------------------------------------------------------------
open(os.path.join(repo, "calc.py"), "w").write(CALC)
tb = ToolBox(sb)
out, err = tb.run("str_replace", json.dumps({"path": "calc.py", "old_str": "def add(a, b):\n    return a + b", "new_str": "x"}))
results["tools: miss shows the closest region"] = err and "closest match is lines 6-" in out and "return a - b" in out
out, err = tb.run("str_replace", json.dumps({"path": "calc.py", "old_str": "     7\t    return a - b", "new_str": "     7\t    return a + b"}))
results["tools: line-number prefixes stripped"] = (not err and "removed view_file line-number" in out
                                                   and "    return a + b\n" in open(os.path.join(repo, "calc.py")).read())
results["tools: edited region shown"] = "def add(a, b):" in out
out, err = tb.run("bash", json.dumps({"command": "git -c user.email=a@b commit -am x"}))
results["tools: git commit refused"] = err and "not allowed" in out
out, err = tb.run("bash", json.dumps({"command": "git status --short && git diff --stat"}))
results["tools: read-only git allowed"] = not err and "calc.py" in out
start = subprocess.run("git rev-parse HEAD", shell=True, cwd=repo, capture_output=True, text=True).stdout.strip()
subprocess.run("sh -c 'git -c user.email=a@b -c user.name=t commit -qam sneaky'", shell=True, cwd=repo, check=True)
clean_repo_for_patch(sb, start)
results["tools: sneaky commit undone before the diff"] = "return a + b" in repo_diff(sb)
subprocess.run("git checkout -q -- calc.py", shell=True, cwd=repo)

m = recon(sb, {"interface": "- Path: `calc.py`\n- Name: helper_fn\n- Name: add_numbers", "requirements": "use `helper` in `legacy`",
              "problem_statement": ""})
results["recon: map lists paths and names"] = "calc.py: exists" in m and "add_numbers: not found" in m

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
nudge = {"turn": None, "n": 0, "budget": False}
def nudge_model(messages):
    nudge["n"] += 1
    nudge["budget"] |= any(m["role"] == "tool" and "[budget: turn" in m["content"] for m in messages)
    if "still has no changes" in last_user(messages) and nudge["turn"] is None:
        nudge["turn"] = nudge["n"]
        return reply(tc(2, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return a + b"}),
                     tc(3, "finish", {"summary": "fixed"}))
    return reply(tc(1, "bash", {"command": "cat calc.py"}))
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "", "interface": "",
               "sandbox_url": url, "workdir": repo}, lambda p: "a + b" in p, nudge_model)
results["nudge: sent at the first threshold"] = nudge["turn"] == config.NO_EDIT_NUDGE_AT[0] + 1
results["budget: note reaches the model"] = nudge["budget"]
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

# -- review ------------------------------------------------------------------------------------
repo, proc, url = make_repo("review", 18814); procs.append(proc); time.sleep(1.5)
rev = {"asked": 0, "model": None}
def review_model(messages):
    if messages[0]["content"].startswith("You review a patch"):
        rev["asked"] += 1
        assert "a * b" in messages[1]["content"], "the reviewer must see the diff"
        return reply(content="1. Requirement 'add returns a+b': the patch multiplies. Use a + b.")
    if "A reviewer compared" in last_user(messages):
        return reply(tc(2, "str_replace", {"path": "calc.py", "old_str": "return a * b", "new_str": "return a + b"}),
                     tc(3, "finish", {"summary": "fixed per review"}))
    if any(m["role"] == "tool" for m in messages):
        return reply(tc(9, "finish", {"summary": "done"}))
    return reply(tc(1, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return a * b"}))
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "add returns a+b", "interface": "",
               "sandbox_url": url, "workdir": repo}, lambda p: "a + b" in p, review_model)
results["review: asked once"] = rev["asked"] == 1
results["review: first grading already has the fix"] = len(d.evals) == 1 and d.evals[0][0]["tests_failed"] == 0

# -- cands -------------------------------------------------------------------------------------
def cand_repo(name, port):
    repo, proc, url = make_repo(name, port)
    open(os.path.join(repo, ".gitignore"), "w").write("node_modules/\n")
    os.makedirs(os.path.join(repo, "node_modules", "lib")); open(os.path.join(repo, "node_modules", "lib", "x.js"), "w").write("1")
    subprocess.run(f"cd {repo} && git add .gitignore && git -c user.email=a@b -c user.name=t commit -qm ignore", shell=True, check=True)
    procs.append(proc); time.sleep(1.5)
    return repo, url

config.CANDIDATES = 2
config.CANDIDATES_MIN_RUN_LEFT_S = 0
config.CANDIDATE_ROOT = f"{work}/w/cands"
repo, url = cand_repo("cands", 18815)
seen_c = {"c1_main_edit": None, "c1_ls": ""}
def cands_model(messages):
    sysmsg = messages[0]["content"]
    if sysmsg.startswith("You review a patch"):
        return reply(content="LGTM")
    is_c1 = "Your checkout is" in sysmsg
    tools_done = [m for m in messages if m["role"] == "tool"]
    if not tools_done:
        if is_c1:   # absolute path into the MAIN checkout: must land in the copy
            return reply(tc(1, "str_replace", {"path": f"{repo}/calc.py", "old_str": "return a - b", "new_str": "return a + b"}),
                         tc(2, "bash", {"command": f"ls {repo}/node_modules/lib && cat {repo}/calc.py | grep return"}))
        time.sleep(0.5)
        return reply(tc(1, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return a * b"}))
    if is_c1 and seen_c["c1_main_edit"] is None:
        seen_c["c1_ls"] = tools_done[-1]["content"]
        seen_c["c1_main_edit"] = open(os.path.join(repo, "calc.py")).read()
    return reply(tc(9, "finish", {"summary": "done"}))
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "", "interface": "",
               "sandbox_url": url, "workdir": repo}, lambda p: "a + b" in p, cands_model)
cand_dir = f"{config.CANDIDATE_ROOT}/cand1"
results["cands: copy made, node_modules linked"] = os.path.islink(os.path.join(cand_dir, "node_modules"))
results["cands: candidate 1 path redirected (main untouched by it)"] = (
    seen_c["c1_main_edit"] is not None and "a + b" not in seen_c["c1_main_edit"] and "x.js" in seen_c["c1_ls"])
results["cands: final passes (graded as candidates finish)"] = (1 <= len(d.evals) <= 2 and d.final[1]["tests_failed"] == 0)
results["cands: graded patches only touch calc.py"] = all(
    [l for l in p.splitlines() if l.startswith("diff --git")] == ["diff --git a/calc.py b/calc.py"] for _, p in d.evals)

config.CANDIDATE_ROOT = f"{work}/w/dedup"
repo, url = cand_repo("dedup", 18816)
def dedup_model(messages):
    if messages[0]["content"].startswith("You review a patch"):
        return reply(content="LGTM")
    if not any(m["role"] == "tool" for m in messages):
        return reply(tc(1, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return a + b"}))
    return reply(tc(9, "finish", {"summary": "done"}))
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "", "interface": "",
               "sandbox_url": url, "workdir": repo}, lambda p: "a + b" in p, dedup_model)
results["dedup: same patch graded once"] = len(d.evals) == 1 and d.final[1]["tests_failed"] == 0

config.CANDIDATE_ROOT = f"{work}/w/early"
repo, url = cand_repo("early", 18817)
slow = {"calls": 0}
def early_model(messages):
    sysmsg = messages[0]["content"]
    if sysmsg.startswith("You review a patch"):
        return reply(content="LGTM")
    if "Your checkout is" in sysmsg:                # candidate 1 just keeps reading
        slow["calls"] += 1
        time.sleep(0.2)
        return reply(tc(1, "bash", {"command": "cat calc.py"}))
    if not any(m["role"] == "tool" for m in messages):
        return reply(tc(1, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return a + b"}))
    return reply(tc(9, "finish", {"summary": "done"}))
t0 = time.time()
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "", "interface": "",
               "sandbox_url": url, "workdir": repo}, lambda p: "a + b" in p, early_model)
results["early: one grading, passes"] = len(d.evals) == 1 and d.final[1]["tests_failed"] == 0
results["early: candidate 1 stopped well before its turn limit"] = slow["calls"] < config.MAX_TURNS // 2

# -- iface ----------------------------------------------------------------------------------------
ents = interface_entries("- Path: `a/b.py`\n- Name: Foo.bar\n- Type: method\n\n- Path: c.ts\n\n- Name: `baz`\n\n- Type: function\n- Name: Connection error dialog")
results["iface: entries parsed"] = ents == [("a/b.py", "Foo.bar", "method"), ("c.ts", "baz", "function")]
results["iface: no interface, no entries"] = interface_entries("No new interfaces are introduced.") == []
repo, proc, url = make_repo("iface", 18819); procs.append(proc); time.sleep(1.5)
ifc = {"gate_msg": ""}
def iface_model(messages):
    sysmsg = messages[0]["content"]
    if sysmsg.startswith("You review a patch"):
        return reply(content="LGTM")
    last = last_user(messages)
    if "Interface check" in last and not ifc["gate_msg"]:
        ifc["gate_msg"] = last
        return reply(tc(2, "str_replace", {"path": "calc.py", "old_str": "def legacy():", "new_str": "def add_numbers(a, b):\n    return a + b\n\ndef legacy():"}),
                     tc(3, "finish", {"summary": "added add_numbers"}))
    if any(m["role"] == "tool" for m in messages):
        return reply(tc(9, "finish", {"summary": "done"}))
    return reply(tc(1, "str_replace", {"path": "calc.py", "old_str": "return a - b", "new_str": "return a + b"}))
d = run_agent({"task": 0, "problem_statement": "add subtracts", "requirements": "", "sandbox_url": url, "workdir": repo,
               "interface": "- Path: `calc.py`\n- Name: add_numbers\n- Type: function"},
              lambda p: "def add_numbers" in p, iface_model)
results["iface: missing name sent back to the model"] = "add_numbers" in ifc["gate_msg"] and "not found in calc.py" in ifc["gate_msg"]
results["iface: only the complete patch graded"] = len(d.evals) == 1 and d.final[1]["tests_failed"] == 0

# -- retry policy -------------------------------------------------------------------------------
W = agentic_loop._worth_retry
results["retry: near misses and unrun tests only"] = (
    W({"tests_failed": 1, "tests_total": 2}) and W({"tests_failed": 3, "tests_total": 61})
    and W({"tests_failed": 0, "tests_total": 0}) and not W({"tests_failed": 6, "tests_total": 8})
    and not W({"tests_failed": 61, "tests_total": 61}) and not W(None))

# -- api -----------------------------------------------------------------------------------------
import httpx, openai
live = {"now": 0, "max": 0, "n429": 0}
live_lock = threading.Lock()
def slow_create(model=None, messages=None, **kw):
    with live_lock:
        live["now"] += 1; live["max"] = max(live["max"], live["now"])
    try:
        time.sleep(0.2)
        if messages and messages[0]["content"] == "429" and live["n429"] < 4:
            live["n429"] += 1
            raise openai.RateLimitError("Too many concurrent requests (limit: 8)",
                                        response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)
        return reply(content="ok")
    finally:
        with live_lock:
            live["now"] -= 1
agent = agentic_loop.Agent(FakeDispatcher({}, None))
agent.client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=slow_create)))
fake_st = types.SimpleNamespace(k=0, messages=[], logger=Logger(), iterations=0, usage={})
got = []
ths = [threading.Thread(target=lambda: got.append(agent._call_with_retries(fake_st, "standard", messages=[{"role": "user", "content": "hi"}], tools=False)))
       for _ in range(12)]
for t in ths: t.start()
for t in ths: t.join()
results["api: never more than MAX_CONCURRENT_CALLS in flight"] = live["max"] <= config.MAX_CONCURRENT_CALLS and len(got) == 12 and all(got)
config.RATE_LIMIT_WAIT_S, config.API_RETRIES = 0.01, 2
r = agent._call_with_retries(fake_st, "standard", messages=[{"role": "system", "content": "429"}], tools=False)
results["api: 429s waited out without using retries"] = r is not None and live["n429"] == 4

for p in procs: p.terminate()
for name, ok in results.items():
    print(("ok   " if ok else "FAIL ") + name)
print("PASS" if all(results.values()) else "FAIL")
sys.exit(0 if all(results.values()) else 1)
