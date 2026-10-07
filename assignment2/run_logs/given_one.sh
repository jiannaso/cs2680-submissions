#!/bin/bash
# usage: given_one.sh LABEL IDX : run the current agent on one given task and evaluate it with an empty pro_eval/
cd "$(dirname "$0")/.."
out=run_logs/given_$1; mkdir -p $out
bash evaluation_scripts/run_task.sh $2 > $out/task_$2.log 2>&1
iid=$(python3 -c "import json;print(list(json.load(open('evaluation_scripts/agent_task_input.json')))[$2])")
cp model_patch_$iid.diff $out/
bash run_logs/eval_saved.sh $out
