# madsLoop v1 (baseline agent)

The agent as it was for the first full run (4/10 on agent_task_input.json),
kept to reproduce the "before" runs of mytest/solved_after_change.json.
Differences from the submitted agent at the repo root:
- src/prompts.py: the original system prompt (no rules on literal data shapes,
  fitting in with existing code/tests, or project-tooling verification);
- src/agentic_loop.py: Tools(workdir) without task_text, so done() has no
  final review against the task.

Run a task with it (outputs land in this folder):

    MADSLOOP_REPO=mytest/harness_v1 TASKS_JSON=mytest/solved_after_change.json \
      bash evaluation_scripts/run_task.sh 0
