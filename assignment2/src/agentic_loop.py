"""The agentic loop and conversation state."""
import json
import os
import sys
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, BadRequestError, OpenAI

from .context import COMPACT_AT_TOKENS, compact
from .git_guard import restore_unstaged, start_commit
from .prompts import SYSTEM_PROMPT, initial_user_message
from .tools import TOOL_SCHEMAS, ToolError, Tools, truncate

MAX_ITERATIONS = 100
MAX_WALL_S = 50 * 60  # self-imposed time limit for one run
WRAP_UP_ITERATIONS = 10  # warn the model when this many iterations (or 15% of the time) are left
MAX_NUDGES = 3
MAX_RETRIES = 6
REPEAT_LIMIT = 3  # identical consecutive tool calls before we point it out

NUDGE = ("You replied without a tool call. Continue working using the tools. "
         "If the fix is complete and verified, call done.")
NUDGE_CUT_OFF = ("Your reply was cut off because it was too long, and contained no complete tool call. "
                 "Reply with a single, shorter tool call.")
WRAP_UP = ("\n\n[harness] You are close to the step/time budget. Stop exploring: make sure your fix is "
           "in place in the repository, check it once, then call done.")
REPEATED = ("\n\n[harness] You have made this exact call {n} times in a row and the result will not change. "
            "Try a different approach.")


def _chat(client, model_id, messages, iteration, logger):
    """One model call with exponential backoff on transient errors."""
    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(
                model=model_id, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto",
            )
        except (APIConnectionError, APITimeoutError, APIStatusError) as e:
            status = getattr(e, "status_code", None)
            # Retry network errors, 408, 429 and 5xx; other 4xx (bad key, bad request) will not fix themselves.
            if attempt == MAX_RETRIES - 1 or (status and 400 <= status < 500 and status not in (408, 429)):
                raise
            backoff = min(2 ** attempt * 2, 60)
            logger.event("api_retry", iteration=iteration, error=str(e)[:500], backoff_s=backoff)
            print(f"[madsLoop] api error, retrying in {backoff}s: {e}", file=sys.stderr)
            time.sleep(backoff)


def run_agent(problem: str, workdir: str, model_id: str, logger, client=None) -> str:
    tools = Tools(workdir, task_text=problem)
    base = start_commit(workdir)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(workdir=workdir)},
        {"role": "user", "content": initial_user_message(problem, workdir)},
    ]
    logger.event("run_start", model_id=model_id, workdir=workdir)

    reason, iteration, nudges = "max_iterations", 0, 0
    started, warned = time.monotonic(), False
    last_call, repeats = None, 0
    try:
        if client is None:
            client = OpenAI(base_url="https://api.cs2680.com/v1", api_key=os.environ["CS2680_API_KEY"])
        while iteration < MAX_ITERATIONS:
            elapsed = time.monotonic() - started
            if elapsed > MAX_WALL_S:
                reason = "time_limit"
                break
            iteration += 1
            try:
                resp = _chat(client, model_id, messages, iteration, logger)
            except BadRequestError as e:
                # Most likely the context window overflowed: drop nearly all old outputs and try once more.
                if "context" not in str(e).lower() and "token" not in str(e).lower():
                    raise
                print(f"[madsLoop] request rejected ({e}); compacting hard and retrying", file=sys.stderr)
                compact(messages, keep_recent=2)
                resp = _chat(client, model_id, messages, iteration, logger)
            u = resp.usage
            logger.event("api_request", iteration=iteration,
                         prompt_tokens=getattr(u, "prompt_tokens", None) or 0,
                         completion_tokens=getattr(u, "completion_tokens", None) or 0,
                         total_tokens=getattr(u, "total_tokens", None) or 0)

            if (getattr(u, "prompt_tokens", 0) or 0) > COMPACT_AT_TOKENS:
                saved = compact(messages)
                print(f"[madsLoop] prompt at {u.prompt_tokens} tokens; elided {saved} chars of old tool output",
                      file=sys.stderr)

            choice = resp.choices[0]
            msg = choice.message
            tool_calls = msg.tool_calls or []
            assistant = {"role": "assistant", "content": msg.content or ""}
            if tool_calls:
                assistant["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ]
            messages.append(assistant)

            if not tool_calls:
                # The model often stops early with prose; push it back to work a few times.
                if nudges >= MAX_NUDGES:
                    reason = "no_tool_calls"
                    break
                nudges += 1
                messages.append({"role": "user",
                                 "content": NUDGE_CUT_OFF if choice.finish_reason == "length" else NUDGE})
                continue

            nudges = 0  # the limit is on consecutive replies without a tool call
            # Harness notes ride on the last tool result of the turn, so the log shows exactly what the model saw.
            note = ""
            if not warned and (MAX_ITERATIONS - iteration <= WRAP_UP_ITERATIONS or elapsed > 0.85 * MAX_WALL_S):
                note, warned = WRAP_UP, True
                tools.budget_low = True

            finished = False
            for i, tc in enumerate(tool_calls):
                name = tc.function.name
                call_key = (name, tc.function.arguments)
                repeats = repeats + 1 if call_key == last_call else 1
                last_call = call_key
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments must be a JSON object")
                    parse_error = None
                except ValueError as e:
                    args, parse_error = {"_raw": tc.function.arguments}, e
                logger.event("tool_call", iteration=iteration, tool_name=name, arguments=args)
                if parse_error is not None:
                    result, is_error = f"Error: could not parse tool arguments as JSON: {parse_error}", True
                else:
                    try:
                        result, is_error = tools.execute(name, args), False
                    except ToolError as e:
                        result, is_error = f"Error: {e}", True
                    except KeyError as e:
                        result, is_error = f"Error: missing required argument {e}", True
                    except Exception as e:
                        result, is_error = f"Error: {type(e).__name__}: {e}", True
                result = truncate(result)
                if repeats >= REPEAT_LIMIT:
                    result += REPEATED.format(n=repeats)
                if i == len(tool_calls) - 1:
                    result += note
                logger.event("tool_result", iteration=iteration, tool_name=name, result=result,
                             is_error=is_error)
                # Every tool call gets exactly one result message, even after done.
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                if name == "done" and not is_error:
                    finished = True
            if finished:
                reason = "done"
                break
    except Exception as e:
        print(f"[madsLoop] fatal: {type(e).__name__}: {e}", file=sys.stderr)
        reason = "error"
    except BaseException:  # Ctrl-C, or SIGTERM from `docker stop` (see madsLoop.py)
        reason = "error"  # the schema allows only done / no_tool_calls / error / a self-imposed limit
        raise
    finally:
        # Runs on every exit path, so each log ends with run_end and the fix stays unstaged.
        restore_unstaged(workdir, base)
        logger.event("run_end", reason=reason, num_iterations=iteration)
        logger.close()
    return reason
