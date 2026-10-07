#!/bin/bash
# usage: eval_saved.sh DIR : evaluate the model_patch_*.diff files saved in DIR with an EMPTY pro_eval/
# (swe_bench_pro_eval.py skips any instance whose output already exists in pro_eval/).
cd "$(dirname "$0")/.."
dir=$1
[ -d pro_eval ] && mv pro_eval "run_logs/pro_eval_old_$(date +%s)"
python3 - "$dir" <<'PY'
import json, pathlib, sys
d = pathlib.Path(sys.argv[1])
tasks = json.load(open('evaluation_scripts/agent_task_input.json'))
preds = [{"instance_id": i, "model_patch": (d / f"model_patch_{i}.diff").read_text(), "prefix": "madsLoop"}
         for i in tasks if (d / f"model_patch_{i}.diff").exists()]
json.dump(preds, open('patches.json', 'w'), indent=2)
print(len(preds), "patches from", d)
PY
bash evaluation_scripts/evaluate.sh > "$dir/eval_fresh.log" 2>&1
rm -rf "$dir/pro_eval"; mv pro_eval "$dir/pro_eval"
grep -E "^(RESOLVED|unresolved)" "$dir/eval_fresh.log" | sed "s#^#$(basename $dir) #" | cut -c1-90
