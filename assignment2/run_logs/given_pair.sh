#!/bin/bash
# usage: given_pair.sh LABEL : run the current agent on openlibrary (5) and protonmail (9), evaluate both
cd "$(dirname "$0")/.."
out=run_logs/given_$1; mkdir -p $out
for i in 5 9; do bash evaluation_scripts/run_task.sh $i > $out/task_$i.log 2>&1 & done; wait
cp model_patch_instance_internetarchive*.diff model_patch_instance_protonmail*.diff $out/
python3 - <<'PY'
import json, pathlib
tasks = json.load(open('evaluation_scripts/agent_task_input.json'))
preds = [{"instance_id": i, "model_patch": pathlib.Path(f"model_patch_{i}.diff").read_text(), "prefix": "madsLoop"}
         for i in tasks if ('openlibrary' in i or 'protonmail' in i)]
json.dump(preds, open('patches.json', 'w'), indent=2)
PY
bash evaluation_scripts/evaluate.sh > $out/eval.log 2>&1
cp -R pro_eval $out/pro_eval
grep -E "^(RESOLVED|unresolved)" $out/eval.log | sed "s/^/$1 /" >> run_logs/given_pair_summary.txt
