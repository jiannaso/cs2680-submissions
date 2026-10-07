#!/bin/bash
# usage: round.sh LABEL IDX... : run the current agent on mytest tasks in parallel, save + score patches
cd "$(dirname "$0")/../.."
label=$1; shift
mkdir -p run_logs/mytest_final/$label
for i in "$@"; do TASKS_JSON=mytest/solved_after_change.json bash evaluation_scripts/run_task.sh $i > run_logs/mytest_final/$label/task_$i.log 2>&1 & done; wait
sleep 5
for i in "$@"; do
  iid=$(python3 -c "import json;print(list(json.load(open('mytest/solved_after_change.json')))[$i])")
  cp model_patch_$iid.diff run_logs/mytest_final/$label/
  echo "$label $iid: $(bash mytest/verify.sh $iid run_logs/mytest_final/$label/model_patch_$iid.diff 2>&1 | grep -E '^== fail_to_pass|^== pass_to_pass|RESOLVED|unresolved' | tr '\n' ' ')"
done >> run_logs/mytest_final/summary.txt
