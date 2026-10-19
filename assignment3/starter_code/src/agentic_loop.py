"""Your agent. madsOpt.py (course code) builds the dispatcher client and calls

    Agent(dispatcher).run()

Design of this first version:
- NUM_WORKERS threads, each opening a task with next_task() and solving it, so up to 5 tasks
  (and their gradings) run at once;
- per task, a tool-calling loop (bash, view_file, str_replace, create_file, finish) on the
  task's Sandbox, then extract_patch() + evaluate();
- if hidden tests still fail and time allows, continue_task() and one more attempt with the
  grading result as feedback; if the retry scores worse, the better patch is graded again;
- every model call and tool call goes to the task's trace (dispatcher.logger(k)).
"""

import os
import sys
import threading
import time
import traceback

from . import config, prompts
from .sandbox import Sandbox
from .tools import TOOL_SPECS, ToolBox, clean_repo_for_patch


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


def _trim_context(messages: list, keep_recent: int):
    """Replace the content of all but the last `keep_recent` tool results with a short note.
    Done in one sweep only when the conversation is too long, so the cached prefix survives
    between sweeps."""
    size = sum(len(m.get("content") or "") + len(str(m.get("tool_calls") or "")) for m in messages)
    if size <= config.CONTEXT_CHAR_LIMIT and keep_recent == config.KEEP_RECENT_TOOL_RESULTS:
        return
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    for i in tool_idx[:-keep_recent] if keep_recent else tool_idx:
        if not messages[i]["content"].startswith("[old output removed"):
            messages[i]["content"] = "[old output removed to save context; rerun the tool if needed]"


class TaskState:
    def __init__(self, task: dict, dispatcher):
        self.k = task["task"]
        self.task = task
        self.logger = dispatcher.logger(self.k)
        self.sb = Sandbox(task["sandbox_url"], task["workdir"])
        self.tools = ToolBox(self.sb)
        self.iterations = 0
        self.started = time.time()
        self.messages = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT.format(workdir=task["workdir"])},
            {"role": "user", "content": prompts.TASK_PROMPT.format(
                problem_statement=task.get("problem_statement") or "",
                requirements=task.get("requirements") or "(none given)",
                interface=task.get("interface") or "(none given)")},
        ]


class Agent:
    def __init__(self, dispatcher):
        from openai import OpenAI                       # installed in the agent container

        self.dispatcher = dispatcher
        self.client = OpenAI(base_url=os.environ["CS2680_BASE_URL"], api_key=os.environ["CS2680_API_KEY"],
                             max_retries=0, timeout=600)
        self.model_id = os.environ[f"CS2680_MODEL_{config.MODEL_TIER}"]
        self.retry_model_id = os.environ[f"CS2680_MODEL_{config.RETRY_MODEL_TIER}"]
        self.run_started = None
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
        st = TaskState(task, self.dispatcher)
        k = st.k
        best_ev, best_patch, last_ev = None, None, None
        reason = "done"

        attempt = 1
        while True:
            if attempt == 1:
                turns, model = config.MAX_TURNS, self.model_id
                deadline = min(st.started + config.TASK_WALL_S, self._soft_deadline())
            else:
                turns, model = config.RETRY_TURNS, self.retry_model_id
                deadline = min(time.time() + config.TASK_WALL_S // 2, self._soft_deadline())
            reason = self._agent_loop(st, model, turns, deadline)

            cleanup = clean_repo_for_patch(st.sb)
            _debug(f"task {k} attempt {attempt}: loop ended ({reason}); changed files:\n{cleanup}")
            size = self.dispatcher.extract_patch(k)
            if not size:
                _debug(f"task {k}: empty patch")
                if best_patch is None:
                    reason = f"{reason}; empty patch"
                break
            patch = self._read_patch(k)
            try:
                last_ev = self.dispatcher.evaluate(k)
            except DispatcherError as e:
                _debug(f"task {k}: evaluate refused: {e}")
                break
            if best_ev is None or _score(last_ev) > _score(best_ev):
                best_ev, best_patch = last_ev, patch
            if _solved(last_ev):
                break
            if attempt >= config.MAX_ATTEMPTS:
                break
            if config.RUN_LIMIT_S - self._run_elapsed() < config.RETRY_MIN_RUN_LEFT_S:
                break
            extra = (" The tests did not run or could not be collected: check for syntax or "
                     "import errors." if not last_ev.get("tests_total") else "")
            st.messages.append({"role": "user", "content": prompts.RETRY_PROMPT.format(
                failed=last_ev.get("tests_failed"), total=last_ev.get("tests_total"), extra=extra)})
            self.dispatcher.continue_task(k)            # deletes the graded patch: nothing counts until the next evaluate
            last_ev = None
            attempt += 1

        # The task's LAST evaluation counts: if a retry made things worse (or left no patch),
        # grade the best patch again.
        if (best_patch and (last_ev is None or _score(last_ev) < _score(best_ev))
                and config.RUN_LIMIT_S - self._run_elapsed() > 40 * 60):
            _debug(f"task {k}: retry scored worse; regrading the best patch")
            try:
                if last_ev is not None:
                    self.dispatcher.continue_task(k)
                self.dispatcher.submit_patch(k, best_patch)
                self.dispatcher.evaluate(k)
            except DispatcherError as e:
                _debug(f"task {k}: regrade failed: {e}")
        self.dispatcher.done(k, reason, st.iterations)

    def _read_patch(self, k: int):
        try:
            with open(self.dispatcher.patch_path(k)) as f:
                return f.read()
        except OSError:
            return None

    def _agent_loop(self, st: TaskState, model_id: str, max_turns: int, deadline: float) -> str:
        """Run the model with tools until it calls finish, runs out of turns or time. Returns why it stopped."""
        idle = 0
        for turn in range(max_turns):
            if time.time() > deadline:
                return "time limit"
            left = max_turns - turn
            if left == 3:
                st.messages.append({"role": "user", "content": prompts.LAST_TURNS_WARNING.format(left=left)})
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
                st.messages.append({"role": "user", "content": prompts.NO_TOOL_NUDGE})
                continue
            idle = 0

            finished = False
            for tc in msg.tool_calls:
                name, raw = tc.function.name, tc.function.arguments or "{}"
                st.logger.tool_call(st.iterations, name, raw)
                result, is_error = st.tools.run(name, raw)
                st.logger.tool_result(st.iterations, name, result, is_error)
                st.messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                if name == "finish":
                    finished = True
            if finished:
                return "finish"
        return "turn limit"

    def _call_with_retries(self, st: TaskState, model_id: str):
        import openai
        backoff = config.API_BACKOFF_S
        for attempt in range(config.API_RETRIES):
            try:
                return call_model(self.client, model_id, st.messages, st.logger, st.iterations,
                                  tools=TOOL_SPECS, **config.EXTRA_MODEL_ARGS)
            except (openai.AuthenticationError, openai.PermissionDeniedError) as e:   # retrying cannot help
                st.logger.api_retry(st.iterations, f"{type(e).__name__}: {e}", 0)
                _debug(f"task {st.k}: API refused the key: {e}")
                return None
            except openai.BadRequestError as e:          # most likely the context is too long
                st.logger.api_retry(st.iterations, f"BadRequestError: {e}", 0)
                if attempt >= 1:
                    _debug(f"task {st.k}: bad request: {e}")
                    return None
                _trim_context(st.messages, keep_recent=2)
            except Exception as e:                       # rate limit, timeout, proxy hiccup
                st.logger.api_retry(st.iterations, f"{type(e).__name__}: {e}", backoff)
                _debug(f"task {st.k}: API error ({type(e).__name__}: {e}); retrying in {backoff}s")
                time.sleep(backoff)
                backoff *= 2
        return None
