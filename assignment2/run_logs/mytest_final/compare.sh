#!/bin/bash
# usage: [BEFORE=mytest/harness_x] compare.sh LABEL IDX... : run the "before" agent (default mytest/harness_v1)
# and the current agent on the same tasks, score both
cd "$(dirname "$0")/../.."
BEFORE="${BEFORE:-mytest/harness_v1}"
label=$1; shift
out=run_logs/mytest_final/cmp_$label; mkdir -p $out
for i in "$@"; do
  MADSLOOP_REPO=$BEFORE TASKS_JSON=mytest/solved_after_change.json bash evaluation_scripts/run_task.sh $i > $out/v1_task_$i.log 2>&1 &
  TASKS_JSON=mytest/solved_after_change.json bash evaluation_scripts/run_task.sh $i > $out/final_task_$i.log 2>&1 &
done; wait; sleep 5
for i in "$@"; do
  iid=$(python3 -c "import json;print(list(json.load(open('mytest/solved_after_change.json')))[$i])")
  cp $BEFORE/model_patch_$iid.diff $out/v1_$iid.diff; cp model_patch_$iid.diff $out/final_$iid.diff
  for v in v1 final; do
    echo "$label $v $iid: $(bash mytest/verify.sh $iid $out/${v}_$iid.diff 2>&1 | grep -E '^== fail_to_pass|^== pass_to_pass|RESOLVED|unresolved' | tr '\n' ' ')"
  done
done >> run_logs/mytest_final/compare_summary.txt
