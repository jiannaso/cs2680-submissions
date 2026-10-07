#!/bin/bash
# usage: v1only.sh LABEL IDX... : run only the v1 agent (mytest/harness_v1) on tasks in parallel and score
cd "$(dirname "$0")/../.."
out=run_logs/mytest_final/v1_$1; mkdir -p $out; shift
for i in "$@"; do MADSLOOP_REPO=mytest/harness_v1 TASKS_JSON=mytest/solved_after_change.json bash evaluation_scripts/run_task.sh $i > $out/task_$i.log 2>&1 & done; wait; sleep 5
for i in "$@"; do iid=$(python3 -c "import json;print(list(json.load(open('mytest/solved_after_change.json')))[$i])")
  cp mytest/harness_v1/model_patch_$iid.diff $out/; echo "$(basename $out) $iid: $(bash mytest/verify.sh $iid $out/model_patch_$iid.diff 2>&1 | grep -E '^== fail_to_pass|^== pass_to_pass|RESOLVED|unresolved' | sed 's/ passing//; s/== //g' | tr '\n' ' ')"; done >> run_logs/mytest_final/v1only_summary.txt
