"""The agentic loop and conversation state."""
import json
import os
import sys
import time

from openai import OpenAI

from .prompts import SYSTEM_PROMPT, initial_user_message
from .tools import TOOL_SCHEMAS, ToolError, Tools, truncate

MAX_ITERATIONS = 100
MAX_NUDGES = 3
MAX_RETRIES = 6

NUDGE = ("You replied without a tool call. Continue working using the tools. "
         "If the fix is complete and verified, call done.")


def _chat(client, model_id, messages, iteration, logger):
    """One model call with exponential backoff on transient errors."""
    for attempt in range(MAX_RETRIES):
        try:
            return client.chat.completions.create(
                model=model_id, messages=messages, tools=TOOL_SCHEMAS, tool_choice="auto",
            )
        except Exception as e:  # network errors, 429, 5xx
            status = getattr(e, "status_code", None)
            # 4xx other than 408/429 (bad key, bad request) will not fix themselves.
            if attempt == MAX_RETRIES - 1 or (status and 400 <= status < 500 and status not in (408, 429)):
                raise
            backoff = min(2 ** attempt * 2, 60)
            logger.event("api_retry", iteration=iteration, error=str(e)[:500], backoff_s=backoff)
            print(f"[madsLoop] api error, retrying in {backoff}s: {e}", file=sys.stderr)
            time.sleep(backoff)


def run_agent(problem: str, workdir: str, model_id: str, logger) -> str:
    client = OpenAI(base_url="https://api.cs2680.com/v1", api_key=os.environ["CS2680_API_KEY"])
    tools = Tools(workdir)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(workdir=workdir)},
        {"role": "user", "content": initial_user_message(problem, workdir)},
    ]
    logger.event("run_start", model_id=model_id, workdir=workdir)

    reason, iteration, nudges = "max_iterations", 0, 0
    try:
        while iteration < MAX_ITERATIONS:
            iteration += 1
            resp = _chat(client, model_id, messages, iteration, logger)
            u = resp.usage
            logger.event("api_request", iteration=iteration, prompt_tokens=u.prompt_tokens,
                         completion_tokens=u.completion_tokens, total_tokens=u.total_tokens)

            msg = resp.choices[0].message
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
                messages.append({"role": "user", "content": NUDGE})
                continue

            finished = False
            for tc in tool_calls:
                name = tc.function.name
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

    logger.event("run_end", reason=reason, num_iterations=iteration)
    logger.close()
    return reason
