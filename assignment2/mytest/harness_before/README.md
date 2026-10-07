# madsLoop 7/10 agent (frozen copy)

A frozen copy of the agent (madsLoop.py, src/, docker_env.sh) as it was when it
scored 7/10 on evaluation_scripts/agent_task_input.json (run 4). It is identical to
the submitted agent at the repo root; it was frozen so that experimental changes on
top of it could be compared against it. None of those experiments improved on it,
and all were reverted (see ../candidates/README.md).

Reproduce a run (outputs land in this folder):

    MADSLOOP_REPO=mytest/harness_before TASKS_JSON=mytest/unsolved.json \
      bash evaluation_scripts/run_task.sh <index>
