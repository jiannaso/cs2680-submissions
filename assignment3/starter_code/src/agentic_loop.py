"""Your agent. madsOpt.py (course code) builds the dispatcher client and calls

    Agent(dispatcher).run()

Design of this first version:
- NUM_WORKERS threads, each opening a task with next_task() and solving it, so up to 5 tasks
  (and their gradings) run at once;
- per task, CANDIDATES independent tool-calling loops (bash, view_file, str_replace, create_file,
  finish) run in parallel, each in its own copy of the repository (the task checkout plus git
  worktrees under /work); each distinct patch is graded as soon as its candidate finishes, the
  first passing one stops the others, and retries continue from the best candidate;
- if hidden tests still fail and time allows, continue_task() and one more attempt with the
  grading result as feedback; if the retry scores worse, the better patch is graded again;
- once the conversation passes COMPACT_AT_CHARS, the older turns are summarized by a model and
  replaced by that summary (the recent turns stay verbatim);
- after each task, the expert model rewrites a shared playbook of lessons from the task's outcome
  and actions; tasks opened later get it in their system prompt;
- before every evaluate(), a build/lint gate compiles the changed files (Go packages, Python
  syntax and undefined names) and gives the model a few turns to fix what it finds;
- before every evaluate(), the expert model also reviews the diff against the specification;
  real gaps it reports go back to the model for a few turns (then the build gate runs again);
- a model that explores for NO_EDIT_NUDGE_AT turns without changing anything is told to edit;
- a retry that ends with the same patch as the graded one is not graded again;
- every model call and tool call goes to the task's trace (dispatcher.logger(k)).
"""

import os
import queue
import sys
import threading
import time
import traceback

from . import config, prompts
from .sandbox import Sandbox
from .tools import TOOL_SPECS, ToolBox, check_build, clean_repo_for_patch, make_candidate_dir, repo_diff


def _debug(msg: str):
    print(f"[agent] {msg}", file=sys.stderr, flush=True)


def call_model(client, model_id: str, messages: list, logger, iteration: int, **kwargs):
    """One chat-completions request to the course API, logged in the task's trace."""
    response = client.chat.completions.create(model=model_id, messages=messages, **kwargs)
    usage = response.usage
    logger.api_request(iteration,
                       getattr(usage, "prompt_tokens", None),
                       getattr(usage, "completion_tokens", None),
                       getattr(usage, "total_tokens", None),
                       model_id)
    return response


def _score(ev):
    """Sort key for an evaluation: solved first, then fewer failing tests. None = no patch."""
    if not ev or not ev.get("tests_total"):
        return (0, float("-inf"))
    return (1 if ev["tests_failed"] == 0 else 0, -ev["tests_failed"])


def _solved(ev) -> bool:
    return bool(ev) and ev.get("tests_total", 0) > 0 and ev.get("tests_failed") == 0


def _assistant_dict(msg) -> dict:
    """The model's reply as a plain message dict to send back in later requests."""
    d = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name,
                                         "arguments": tc.function.arguments or "{}"}}
                           for tc in msg.tool_calls]
    return d


def _size(messages: list) -> int:
    return sum(len(m.get("content") or "") + len(str(m.get("tool_calls") or "")) for m in messages)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit * 2 // 3]}\n[... {len(text) - limit} characters cut ...]\n{text[-(limit // 3):]}"


def _render_transcript(messages: list, limit: int) -> str:
    """Messages as plain text for a summarizer: tool calls and results, shortened."""
    lines = []
    for m in messages:
        if m["role"] == "assistant":
            if m.get("content"):
                lines.append(f"AGENT: {_clip(m['content'], 1500)}")
            for tc in m.get("tool_calls") or []:
                lines.append(f"CALL {tc['function']['name']}: {_clip(tc['function']['arguments'], 1200)}")
        elif m["role"] == "tool":
            lines.append(f"RESULT: {_clip(m.get('content') or '', 2000)}")
        else:
            lines.append(f"{m['role'].upper()}: {_clip(m.get('content') or '', 2000)}")
    return _clip("\n".join(lines), limit)


def _trim_context(messages: list, keep_recent: int):
    """Replace the content of all but the last `keep_recent` tool results with a short note.
    Done in one sweep only when the conversation is too long, so the cached prefix survives
    between sweeps."""
    size = _size(messages)
    if size <= config.CONTEXT_CHAR_LIMIT and keep_recent == config.KEEP_RECENT_TOOL_RESULTS:
        return
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    for i in tool_idx[:-keep_recent] if keep_recent else tool_idx:
        if not messages[i]["content"].startswith("[old output removed"):
            messages[i]["content"] = "[old output removed to save context; rerun the tool if needed]"


class _LockedLogger:
    """One task's trace, shared by its candidates' threads: one event written at a time."""

    def __init__(self, logger):
        self._logger = logger
        self._lock = threading.Lock()

    def __getattr__(self, name):
        attr = getattr(self._logger, name)
        if not callable(attr):
            return attr

        def locked(*args, **kwargs):
            with self._lock:
                return attr(*args, **kwargs)
        return locked


class TaskState:
    """One candidate's work on a task: its copy of the repository, conversation and counters."""

    def __init__(self, task: dict, logger, playbook: str = "", workdir: str = None, name: str = "c0"):
        self.k = task["task"]
        self.task = task
        self.name = name
        self.logger = logger
        workdir = workdir or task["workdir"]
        self.sb = Sandbox(task["sandbox_url"], workdir)
        self.tools = ToolBox(self.sb, alias_root=task["workdir"])
        self.reason = "not run"                         # why the latest loop ended
        self.graded_patch, self.prev_ev = None, None    # this candidate's last graded patch and result
        self.stop = None                                # threading.Event: another candidate has passed
        try:                                            # HEAD at the start, restored before every diff
            r = self.sb.exec("git rev-parse HEAD", timeout_s=30)
            self.start_sha = r.get("output", "").strip() if not r.get("exit_code") else None
        except Exception:
            self.start_sha = None
        self.iterations = 0
        self.started = time.time()
        self.summary = ""                               # latest compaction summary
        self.actions = []                               # short log of tool calls, for the playbook report
        self.attempts = []                              # (loop end reason, evaluation) per attempt
        self.nudged = set()                             # NO_EDIT_NUDGE_AT thresholds already used
        self.usage = {}                                 # model id -> [input, cached input, output] tokens
        self.reviews = 0                                # expert reviews done so far
        system = prompts.SYSTEM_PROMPT.format(workdir=workdir)
        if workdir != task["workdir"]:
            system += prompts.CANDIDATE_NOTE.format(workdir=workdir, main=task["workdir"])
        if playbook:
            system += prompts.PLAYBOOK_NOTE.format(playbook=playbook)
        self.task_prompt = prompts.TASK_PROMPT.format(
            problem_statement=task.get("problem_statement") or "",
            requirements=task.get("requirements") or "(none given)",
            interface=task.get("interface") or "(none given)")
        self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": self.task_prompt},
        ]


class Agent:
    def __init__(self, dispatcher):
        from openai import OpenAI                       # installed in the agent container

        self.dispatcher = dispatcher
        self.client = OpenAI(base_url=os.environ["CS2680_BASE_URL"], api_key=os.environ["CS2680_API_KEY"],
                             max_retries=0, timeout=600)
        self.model_id = os.environ[f"CS2680_MODEL_{config.MODEL_TIER}"]
        self.retry_model_id = os.environ[f"CS2680_MODEL_{config.RETRY_MODEL_TIER}"]
        self.compact_model_id = os.environ[f"CS2680_MODEL_{config.COMPACT_MODEL_TIER}"]
        self.playbook_model_id = os.environ[f"CS2680_MODEL_{config.PLAYBOOK_MODEL_TIER}"]
        self.review_model_id = os.environ[f"CS2680_MODEL_{config.REVIEW_MODEL_TIER}"]
        self.playbook = ""
        self._playbook_lock = threading.Lock()
        self._pending_reports = []                      # reports queued while another update ran
        self.run_started = None
        self.cost = 0.0                                 # estimated $ of the whole run so far
        self._lock = threading.Lock()
        self._stop = threading.Event()                  # set on DispatcherShutdown or no tasks left

    # -- time -------------------------------------------------------------------------------

    def _run_elapsed(self) -> float:
        return time.time() - self.run_started if self.run_started else 0.0

    def _soft_deadline(self) -> float:
        return (self.run_started or time.time()) + config.RUN_SOFT_LIMIT_S

    # -- scheduling -------------------------------------------------------------------------

    def run(self):
        workers = [threading.Thread(target=self._worker, name=f"worker-{i}", daemon=True)
                   for i in range(config.NUM_WORKERS)]
        for w in workers:
            w.start()
            time.sleep(1)                               # stagger the first next_task() calls
        for w in workers:
            w.join()
        _debug(f"run done: estimated cost ${self.cost:.2f}")

    def _worker(self):
        from dispatcher import DispatcherError, DispatcherShutdown
        refusals = 0
        while not self._stop.is_set():
            if self.run_started and time.time() > self._soft_deadline():
                _debug("soft run limit reached: opening no more tasks")
                return
            try:
                task = self.dispatcher.next_task()
            except DispatcherShutdown:
                self._stop.set()
                return
            except DispatcherError as e:                # e.g. 5 tasks live: wait and retry
                refusals += 1
                if refusals > 120:
                    _debug(f"next_task keeps failing ({e}); worker stops")
                    return
                time.sleep(config.NEXT_TASK_RETRY_S)
                continue
            refusals = 0
            if task is None:
                self._stop.set()
                return
            with self._lock:
                if self.run_started is None:
                    self.run_started = time.time()
            try:
                self.solve(task)
            except DispatcherShutdown:
                self._stop.set()
                return
            except Exception as e:                      # one broken task must not end the run
                _debug(f"task {task.get('task')}: {type(e).__name__}: {e}\n{traceback.format_exc()}")
                try:
                    self.dispatcher.done(task["task"], f"error: {type(e).__name__}", 0)
                except DispatcherShutdown:
                    self._stop.set()
                    return
                except Exception:
                    pass

    # -- one task ---------------------------------------------------------------------------

    def solve(self, task: dict):
        from dispatcher import DispatcherError
        k = task["task"]
        logger = _LockedLogger(self.dispatcher.logger(k))
        cands = [TaskState(task, logger, self.playbook)]
        cands += self._extra_candidates(task, logger, cands[0])

        # attempt 1: every candidate works on its own copy at the same time
        stop, finished = threading.Event(), queue.Queue()
        for st in cands:
            st.stop = stop

        def first(st):
            try:
                self._first_attempt(st)
            finally:
                finished.put(st)
        threads = [threading.Thread(target=first, args=(st,), daemon=True) for st in cands]
        for t in threads:
            t.start()

        graded = False                                  # a graded patch sits in the task's folder
        best = None                                     # (candidate, patch, evaluation)
        last_ev = None

        def grade(st, patch):
            nonlocal graded, best, last_ev
            if graded:
                self.dispatcher.continue_task(k)        # deletes the graded patch before the next one
                graded = False
            self.dispatcher.submit_patch(k, patch)
            try:
                ev = self.dispatcher.evaluate(k)
            except DispatcherError as e:
                _debug(f"task {k}: evaluate refused: {e}")
                return None
            graded, last_ev = True, ev
            st.attempts[-1][1] = ev
            st.graded_patch, st.prev_ev = patch, ev
            if best is None or _score(ev) > _score(best[2]):
                best = (st, patch, ev)
            return ev

        # grade each distinct patch as soon as its candidate is done; the first that passes stops the rest
        seen = set()
        for _ in cands:
            st = finished.get()
            if stop.is_set():
                continue
            patch = repo_diff(st.sb)
            if not patch.strip() or patch in seen:
                continue
            seen.add(patch)
            _debug(f"task {k}: grading candidate {st.name} ({st.reason})")
            ev = grade(st, patch)
            if ev is not None and _solved(ev):
                stop.set()
        for t in threads:
            t.join()
        for st in cands:
            st.stop = None                              # retries of the best candidate must not stop

        st = best[0] if best else cands[0]
        reason = st.reason if best else f"{st.reason}; empty patch"
        attempt = 1
        while (best and not _solved(best[2]) and attempt < config.MAX_ATTEMPTS
               and config.RUN_LIMIT_S - self._run_elapsed() >= config.RETRY_MIN_RUN_LEFT_S):
            attempt += 1
            ev = st.prev_ev
            extra = (" The tests did not run or could not be collected: check for syntax or "
                     "import errors." if not ev.get("tests_total") else "")
            st.messages.append({"role": "user", "content": prompts.RETRY_PROMPT.format(
                failed=ev.get("tests_failed"), total=ev.get("tests_total"), extra=extra)})
            deadline = min(time.time() + config.TASK_WALL_S // 2, self._soft_deadline())
            reason = self._agent_loop(st, self.retry_model_id, config.RETRY_TURNS, deadline)
            reason = self._build_gate(st, self.retry_model_id, reason)
            reason = self._review(st, self.retry_model_id, reason, ev)
            st.attempts.append([reason, None])
            cleanup = clean_repo_for_patch(st.sb, st.start_sha)
            _debug(f"task {k} candidate {st.name} attempt {attempt}: loop ended ({reason}); changed files:\n{cleanup}")
            patch = repo_diff(st.sb)
            if not patch.strip():
                _debug(f"task {k}: empty patch")
                break
            if patch == st.graded_patch:
                # grading it again would only repeat the last result
                _debug(f"task {k}: attempt {attempt} left the patch unchanged; not grading it again")
                st.attempts[-1][0] = f"{reason}; patch unchanged"
                break
            if grade(st, patch) is None:
                break

        # The task's LAST evaluation counts: if it is not the best one, grade the best patch again.
        if (best and last_ev is not None and _score(last_ev) < _score(best[2])
                and config.RUN_LIMIT_S - self._run_elapsed() > 40 * 60):
            _debug(f"task {k}: last grading scored worse; regrading the best patch")
            best[0].attempts.append(["regrade of the best patch", None])
            grade(best[0], best[1])
        if config.PLAYBOOK_ENABLED:
            self._update_playbook(st, last_ev, best[1] if best else None)  # before done(): the trace closes there
        self._report_cost(cands, last_ev)
        self.dispatcher.done(k, reason, sum(c.iterations for c in cands))

    def _extra_candidates(self, task: dict, logger, main: TaskState) -> list:
        """Copies of the repository for candidates 1.. (none when the checkout is not clean at the
        start: a copy at HEAD would then differ from what the graders apply the patch to)."""
        if config.CANDIDATES <= 1 or time.time() > self._soft_deadline() - config.CANDIDATES_MIN_RUN_LEFT_S:
            return []
        try:
            dirty = main.sb.exec("git status --porcelain | head -3", timeout_s=60).get("output", "").strip()
        except Exception:
            return []
        if dirty:
            _debug(f"task {main.k}: checkout not clean at the start; one candidate only")
            return []
        extra = []
        for i in range(1, config.CANDIDATES):
            workdir = make_candidate_dir(main.sb, f"cand{i}", config.CANDIDATE_ROOT)
            if not workdir:
                _debug(f"task {main.k}: could not make a copy for candidate {i}")
                break
            extra.append(TaskState(task, logger, self.playbook, workdir=workdir, name=f"c{i}"))
        return extra

    def _first_attempt(self, st: TaskState):
        try:
            deadline = min(st.started + config.TASK_WALL_S, self._soft_deadline())
            reason = self._agent_loop(st, self.model_id, config.MAX_TURNS, deadline)
            reason = self._build_gate(st, self.model_id, reason)
            st.reason = self._review(st, self.model_id, reason, None)
        except Exception as e:                          # one broken candidate must not end the task
            st.reason = f"error: {type(e).__name__}"
            _debug(f"task {st.k} candidate {st.name}: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        st.attempts.append([st.reason, None])
        try:
            cleanup = clean_repo_for_patch(st.sb, st.start_sha)
        except Exception as e:
            cleanup = f"cleanup failed: {e}"
        _debug(f"task {st.k} candidate {st.name} attempt 1: loop ended ({st.reason}); changed files:\n{cleanup}")

    def _report_cost(self, cands: list, ev):
        usage = {}
        for c in cands:
            for model_id, u in c.usage.items():
                usage.setdefault(model_id, [0, 0, 0])
                for i in range(3):
                    usage[model_id][i] += u[i]
        cost = 0.0
        parts = []
        for model_id, (inp, cached, out) in usage.items():
            price = config.PRICES.get(model_id, config.PRICES["expert"])
            cost += ((inp - cached) * price[0] + cached * price[1] + out * price[2]) / 1e6
            parts.append(f"{model_id} {inp:,} in ({cached:,} cached) {out:,} out")
        with self._lock:
            self.cost += cost
            total = self.cost
        result = "-" if not ev else f"{ev.get('tests_failed')}/{ev.get('tests_total')} failing"
        _debug(f"task {cands[0].k}: {result}, {len(cands)} candidate(s), {sum(c.iterations for c in cands)} turns, "
               f"{(time.time() - cands[0].started) / 60:.1f} min, "
               f"~${cost:.3f} ({'; '.join(parts)}); run so far ~${total:.2f}")

    def _read_patch(self, k: int):
        try:
            with open(self.dispatcher.patch_path(k)) as f:
                return f.read()
        except OSError:
            return None

    def _build_gate(self, st: TaskState, model_id: str, reason: str) -> str:
        """Compile/lint the changed files; on problems, give the model a few turns to fix them."""
        if not config.BUILD_GATE_ENABLED or reason == "api error" or reason.startswith("stopped"):
            return reason
        for _ in range(config.BUILD_GATE_ROUNDS):
            clean_repo_for_patch(st.sb, st.start_sha)
            errors = check_build(st.sb)
            if not errors:
                return reason
            _debug(f"task {st.k}: build gate found problems:\n{errors[:1500]}")
            st.actions.append(f"BUILD GATE: {_clip(errors, 300)}".replace("\n", " "))
            deadline = min(time.time() + config.BUILD_GATE_WALL_S, self._soft_deadline())
            if time.time() > deadline - 60:
                return reason
            st.messages.append({"role": "user", "content": prompts.BUILD_GATE_PROMPT.format(errors=errors)})
            reason = self._agent_loop(st, model_id, config.BUILD_GATE_TURNS, deadline, nudge=False)
            if reason == "api error":
                return reason
        return reason

    def _review(self, st: TaskState, model_id: str, reason: str, graded_ev) -> str:
        """Have the expert model check the diff against the specification; if it reports gaps, give
        the model a few turns to act on them and rerun the build gate."""
        if (not config.REVIEW_ENABLED or reason == "api error" or reason.startswith("stopped")
                or st.reviews >= config.REVIEW_MAX_PER_TASK
                or time.time() > self._soft_deadline() - 5 * 60):
            return reason
        clean_repo_for_patch(st.sb, st.start_sha)
        try:
            diff = st.sb.exec("git add -N . >/dev/null 2>&1; git diff", timeout_s=60).get("output", "")
        except Exception:
            return reason
        if not diff.strip():
            return reason
        st.reviews += 1
        graded = ("" if not graded_ev else
                  f"\nNote: the previous version of this patch was graded: {graded_ev.get('tests_failed')} of "
                  f"{graded_ev.get('tests_total')} hidden tests failed.\n")
        request = [{"role": "system", "content": prompts.REVIEW_SYSTEM},
                   {"role": "user", "content": prompts.REVIEW_REQUEST.format(
                       task=st.task_prompt.replace("Start by exploring the repository.", "").rstrip(), graded=graded, diff=_clip(diff, config.REVIEW_DIFF_CHARS))}]
        response = self._call_with_retries(st, self.review_model_id, messages=request, tools=False)
        text = (response.choices[0].message.content or "").strip() if response and response.choices else ""
        if not text or text.upper().startswith("LGTM"):
            _debug(f"task {st.k}: review: {'LGTM' if text else 'no answer'}")
            return reason
        _debug(f"task {st.k}: review found gaps:\n{text[:1500]}")
        st.actions.append(f"REVIEW: {_clip(text, 300)}".replace("\n", " "))
        st.messages.append({"role": "user", "content": prompts.REVIEW_FEEDBACK.format(review=text)})
        deadline = min(time.time() + config.REVIEW_WALL_S, self._soft_deadline())
        reason = self._agent_loop(st, model_id, config.REVIEW_TURNS, deadline, nudge=False)
        return self._build_gate(st, model_id, reason)

    def _no_edit_nudge(self, st: TaskState, turn: int, max_turns: int):
        """At each NO_EDIT_NUDGE_AT turn, tell a model that has changed nothing yet to start editing."""
        due = [t for t in config.NO_EDIT_NUDGE_AT if turn >= t and t not in st.nudged]
        if not due:
            return
        st.nudged.update(due)
        try:
            changed = st.sb.exec("git status --porcelain | head -5", timeout_s=30).get("output", "").strip()
        except Exception:
            return
        if not changed:
            _debug(f"task {st.k}: no edits after {turn} turns; nudging")
            st.messages.append({"role": "user", "content": prompts.NO_EDIT_NUDGE.format(
                used=turn, left=max_turns - turn)})

    def _agent_loop(self, st: TaskState, model_id: str, max_turns: int, deadline: float,
                    nudge: bool = True) -> str:
        """Run the model with tools until it calls finish, runs out of turns or time. Returns why it stopped."""
        idle = 0
        started, time_warned = time.time(), False
        for turn in range(max_turns):
            if st.stop is not None and st.stop.is_set():
                return "stopped: another candidate passed"
            now = time.time()
            if now > deadline:
                return "time limit"
            left = max_turns - turn
            if left == 3:
                st.messages.append({"role": "user", "content": prompts.LAST_TURNS_WARNING.format(left=left)})
            elif not time_warned and left > 3 and now - started > 0.8 * (deadline - started):
                time_warned = True
                st.messages.append({"role": "user", "content": prompts.TIME_WARNING.format(
                    minutes=max(1, round((deadline - now) / 60)))})
            elif nudge:
                self._no_edit_nudge(st, turn, max_turns)
            if _size(st.messages) > config.COMPACT_AT_CHARS:
                self._compact(st)
            _trim_context(st.messages, config.KEEP_RECENT_TOOL_RESULTS)

            response = self._call_with_retries(st, model_id)
            if response is None:
                return "api error"
            st.iterations += 1
            if not response.choices:
                continue
            msg = response.choices[0].message
            st.messages.append(_assistant_dict(msg))

            if not msg.tool_calls:
                idle += 1
                if idle >= 3:
                    return "model stopped calling tools"
                cut_off = getattr(response.choices[0], "finish_reason", None) == "length"
                st.messages.append({"role": "user",
                                    "content": prompts.CUT_OFF_NUDGE if cut_off else prompts.NO_TOOL_NUDGE})
                continue
            idle = 0

            finished = False
            for tc in msg.tool_calls:
                name, raw = tc.function.name, tc.function.arguments or "{}"
                st.logger.tool_call(st.iterations, name, raw)
                result, is_error = st.tools.run(name, raw)
                st.logger.tool_result(st.iterations, name, result, is_error)
                st.actions.append(f"{name}({_clip(raw, 160)}) -> {'ERROR ' if is_error else ''}"
                                  f"{_clip(result, 160)}".replace("\n", " "))
                st.messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                if name == "finish":
                    finished = True
            if finished:
                return "finish"
        return "turn limit"

    # -- context compaction -----------------------------------------------------------------

    def _compact(self, st: TaskState):
        """Summarize everything between the task prompt and the last COMPACT_KEEP_MESSAGES messages,
        and replace it with the summary (appended to the task prompt). The kept tail starts at an
        assistant message, so no tool result loses its tool call."""
        msgs = st.messages
        cut = max(2, len(msgs) - config.COMPACT_KEEP_MESSAGES)
        while cut > 2 and msgs[cut]["role"] != "assistant":
            cut -= 1
        head = msgs[2:cut]
        if len(head) < 4:                               # too little to summarize: the safety net handles it
            return
        previous = f"Summary of the turns before these:\n{st.summary}\n\n" if st.summary else ""
        request = [{"role": "system", "content": prompts.COMPACT_SYSTEM},
                   {"role": "user", "content": prompts.COMPACT_REQUEST.format(
                       previous=previous,
                       transcript=_render_transcript(head, config.COMPACT_INPUT_CHARS))}]
        response = self._call_with_retries(st, self.compact_model_id, messages=request, tools=False)
        summary = (response.choices[0].message.content or "").strip() if response and response.choices else ""
        if not summary:
            _debug(f"task {st.k}: compaction failed; dropping old tool outputs instead")
            _trim_context(msgs, keep_recent=4)
            return
        before = _size(msgs)
        st.summary = summary
        task_msg = {"role": "user", "content": st.task_prompt + prompts.COMPACTED_NOTE.format(summary=summary)}
        st.messages = [msgs[0], task_msg] + msgs[cut:]
        if _size(st.messages) > config.COMPACT_AT_CHARS * 0.6:     # the kept turns hold big outputs
            _trim_context(st.messages, keep_recent=4)
        _debug(f"task {st.k}: compacted {len(head)} messages, {before:,} -> {_size(st.messages):,} chars")

    # -- playbook ---------------------------------------------------------------------------

    def _report(self, st: TaskState, final_ev, patch) -> str:
        if _solved(final_ev):
            outcome = "SOLVED (all hidden tests pass)"
        elif final_ev and final_ev.get("tests_total"):
            outcome = f"FAILED: {final_ev['tests_failed']} of {final_ev['tests_total']} hidden tests fail"
        elif final_ev:
            outcome = "FAILED: the tests did not run (syntax/import error or broken build)"
        else:
            outcome = "FAILED: no patch"
        attempts = "; ".join(f"attempt {i + 1}: loop ended by {r}, grading "
                             f"{'-' if ev is None else str(ev.get('tests_failed')) + '/' + str(ev.get('tests_total')) + ' failing'}"
                             for i, (r, ev) in enumerate(st.attempts))
        files = [l.split(" b/", 1)[1] for l in (patch or "").splitlines() if l.startswith("diff --git")]
        actions = _clip("\n".join(st.actions), config.PLAYBOOK_DIGEST_CHARS - 2500)
        return (f"## Report on one task\nIssue (start): {_clip(st.task.get('problem_statement') or '', 1200)}\n"
                f"Outcome: {outcome}\nAttempts: {attempts}\nModel turns: {st.iterations}\n"
                f"Files in the patch: {', '.join(files) or 'none'}\nActions:\n{actions}")

    def _update_playbook(self, st: TaskState, final_ev, patch):
        """Let the expert model rewrite the playbook from this task's report (and any reports
        queued while another task was updating it). Never blocks a task for long."""
        try:
            report = self._report(st, final_ev, patch)
        except Exception as e:
            _debug(f"task {st.k}: no playbook report ({e})")
            return
        if config.RUN_LIMIT_S - self._run_elapsed() < config.PLAYBOOK_MIN_RUN_LEFT_S:
            return
        if not self._playbook_lock.acquire(timeout=config.PLAYBOOK_LOCK_WAIT_S):
            with self._lock:
                self._pending_reports.append(report)
            return
        try:
            with self._lock:
                reports = self._pending_reports[-3:] + [report]
                self._pending_reports = []
            request = [{"role": "system", "content": prompts.PLAYBOOK_SYSTEM},
                       {"role": "user", "content": prompts.PLAYBOOK_REQUEST.format(
                           playbook=self.playbook or "(empty: this is the first report)",
                           reports="\n\n".join(reports))}]
            response = self._call_with_retries(st, self.playbook_model_id, messages=request, tools=False)
            text = (response.choices[0].message.content or "").strip() if response and response.choices else ""
            if text:
                if len(text) > config.PLAYBOOK_MAX_CHARS:            # keep whole bullets from the top
                    text = text[:config.PLAYBOOK_MAX_CHARS].rsplit("\n", 1)[0]
                self.playbook = text
                _debug(f"task {st.k}: playbook updated ({len(self.playbook)} chars)")
        finally:
            self._playbook_lock.release()

    # -- model calls --------------------------------------------------------------------------

    @staticmethod
    def _count_usage(st: TaskState, model_id: str, usage):
        if usage is None:
            return
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", None) if details is not None else None) or 0
        u = st.usage.setdefault(model_id, [0, 0, 0])
        u[0] += getattr(usage, "prompt_tokens", 0) or 0
        u[1] += cached if isinstance(cached, int) else 0
        u[2] += getattr(usage, "completion_tokens", 0) or 0

    def _call_with_retries(self, st: TaskState, model_id: str, messages: list = None, tools: bool = True):
        """A logged model call with retries. Default: the task's conversation with the tools."""
        import openai
        backoff = config.API_BACKOFF_S
        extra = {"tools": TOOL_SPECS} if tools else {}
        for attempt in range(config.API_RETRIES):
            try:
                response = call_model(self.client, model_id, st.messages if messages is None else messages,
                                      st.logger, st.iterations, **extra, **config.EXTRA_MODEL_ARGS)
                self._count_usage(st, model_id, getattr(response, "usage", None))
                return response
            except (openai.AuthenticationError, openai.PermissionDeniedError) as e:   # retrying cannot help
                st.logger.api_retry(st.iterations, f"{type(e).__name__}: {e}", 0)
                _debug(f"task {st.k}: API refused the key: {e}")
                return None
            except openai.BadRequestError as e:          # most likely the context is too long
                st.logger.api_retry(st.iterations, f"BadRequestError: {e}", 0)
                if attempt >= 1 or messages is not None:
                    _debug(f"task {st.k}: bad request: {e}")
                    return None
                _trim_context(st.messages, keep_recent=2)
            except Exception as e:                       # rate limit, timeout, proxy hiccup
                st.logger.api_retry(st.iterations, f"{type(e).__name__}: {e}", backoff)
                _debug(f"task {st.k}: API error ({type(e).__name__}: {e}); retrying in {backoff}s")
                time.sleep(backoff)
                backoff *= 2
        return None
