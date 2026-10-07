# madsLoop v0 (the first version of the agent)

The agent exactly as it was when it completed its first end-to-end run (the toy
`mean()` bug, 2026-10-06), reconstructed verbatim from the developer log
(developer_logs/claude_code_session_9061f058.jsonl): the first madsLoop.py and src/
written in the session, plus the two edits made before that run (tool_call logged
before execution; no retry on 4xx). It predates all Part 0.1 / Part 1 work:
no git guard, no repro_check, read_file returns whole files (so the 12k-char output
truncation cuts the middle out of large files), done accepts anything (even an
empty diff), edit_file has no miss hints and rewrites CRLF files, minimal prompt.

Infra-only adaptations (not agent behaviour): madsLoop.py also looks for openai in
/tmp/madsloop_deps, and docker_env.sh is the current one, so v0 runs under the
current run_task.sh. src/logger.py is unchanged from the original.

Run (outputs land in this folder):

    MADSLOOP_REPO=mytest/harness_v0 TASKS_JSON=mytest/solved_after_change.json \
      bash evaluation_scripts/run_task.sh <index>

Status: reconstructed but never run on any task. The course API budget for
Assignment 2 was exhausted (2026-10-07) before its first run could complete.
