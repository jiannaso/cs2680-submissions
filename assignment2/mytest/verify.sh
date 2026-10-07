#!/bin/bash
# Score a patch for one mytest task, like the official evaluation does for SWE-bench Pro.
#
#   bash mytest/verify.sh INSTANCE_ID [PATCH.diff]
#
# Starts a fresh container from the task's docker_image, resets the checkout and
# restores the held-out tests with before_repo_set_cmd, applies the patch (its
# edits to test files are dropped, as in the official evaluation), runs
# selected_test_files_to_run, and prints RESOLVED only if every fail_to_pass test
# passes and no pass_to_pass test regresses. Without a patch it scores the
# untouched base_commit (the baseline, where fail_to_pass should fail).
#
# Task data: mytest/task_test.json (tests) and mytest/{solved_after_change,unsolved}.json
# (docker_image); the held-out tests are mytest/tasks/<instance_id>/test.diff,
# mounted at /task. Build the image first: docker build -t <docker_image> mytest/tasks/<id>/
# Tasks that were only candidates are found the same way under mytest/candidates/.
set -euo pipefail

MYTEST="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IID="${1:?usage: verify.sh INSTANCE_ID [PATCH.diff]}"
PATCH="${2:-}"
PLATFORM_FLAG="${PLATFORM_FLAG:-}"

# Pull what this task needs out of the JSON files (values may be JSON-encoded strings,
# as in evaluation_scripts/task_test.json).
eval "$(python3 - "$MYTEST" "$IID" <<'EOF'
import json, os, shlex, sys
mytest, iid = sys.argv[1:]
def lst(v):
    while isinstance(v, str):
        v = json.loads(v)
    return v
image = None
for name in ("solved_after_change.json", "unsolved.json", "candidates/candidates.json"):
    path = os.path.join(mytest, name)
    if os.path.exists(path):
        image = json.load(open(path)).get(iid, {}).get("docker_image") or image
tests = json.load(open(os.path.join(mytest, "task_test.json")))
cand = os.path.join(mytest, "candidates", "task_test.json")
if iid not in tests and os.path.exists(cand):
    tests = json.load(open(cand))
t = tests[iid]
task_dir = os.path.join(mytest, "tasks", iid)
if not os.path.isdir(task_dir):
    task_dir = os.path.join(mytest, "candidates", "tasks", iid)
print(f"TASK_DIR={shlex.quote(task_dir)}")
if not image:
    sys.exit(f"error: {iid} is in neither solved_after_change.json nor unsolved.json")
print(f"IMAGE={shlex.quote(image)}")
print(f"BEFORE_CMD={shlex.quote(t['before_repo_set_cmd'])}")
print(f"TEST_FILES={shlex.quote(' '.join(lst(t['selected_test_files_to_run'])))}")
EOF
)"

# A fresh directory per run (under the repo: Colima only shares $HOME with containers).
# Reusing one path for back-to-back runs let the container see a stale patch.diff.
KEEP="$MYTEST/results/.verify/$IID"
OUT="$KEEP/run-$(date +%s)-$$-$RANDOM"
mkdir -p "$OUT"
if [[ -n "$PATCH" ]]; then
  [[ -f "$PATCH" ]] || { echo "error: no such patch: $PATCH" >&2; exit 1; }
  # Like the official SWE-bench Pro evaluation, drop binary file hunks (e.g. a core dump
  # an agent run left in the repo): they cannot be applied and are never part of a fix.
  python3 - "$PATCH" "$OUT/patch.diff" <<'PY'
import re, sys
text = open(sys.argv[1], errors="surrogateescape").read()
parts = re.split(r"(?m)^(?=diff --git )", text)
kept = [p for p in parts if not re.search(r"(?m)^(GIT binary patch|Binary files .* differ)$", p)]
if len(kept) != len(parts):
    print(f"== stripped {len(parts) - len(kept)} binary file hunk(s) from the patch")
open(sys.argv[2], "w", errors="surrogateescape").write("".join(kept))
PY
fi

echo "== $IID"
echo "== image: $IMAGE"
echo "== patch: ${PATCH:-<none: base_commit baseline>}"

docker run --rm $PLATFORM_FLAG \
  -v "$TASK_DIR:/task:ro" \
  -v "$OUT:/out" \
  -e BEFORE_CMD="$BEFORE_CMD" -e TEST_FILES="$TEST_FILES" \
  --network none \
  "$IMAGE" bash -c '
    cd /app
    eval "$BEFORE_CMD" >/out/setup.log 2>&1 || { echo "before_repo_set_cmd failed:"; cat /out/setup.log; exit 3; }
    if [ -s /out/patch.diff ]; then
      # Edits to test files are dropped: the held-out tests are what get run.
      if ! git apply --whitespace=nowarn --exclude="tests/*" --exclude="test/*" /out/patch.diff 2>/out/apply.log; then
        echo "PATCH FAILED TO APPLY:"; cat /out/apply.log; exit 4
      fi
    fi
    if [ -f package.json ] && ! command -v pytest >/dev/null 2>&1 && ! python -c "import pytest" 2>/dev/null; then
      # JavaScript/TypeScript repo: run jest and write its results in the same "STATUS id" form
      # as pytest -rA, with ids "<test file> | <full test name>" (as in the SWE-bench Pro JS tasks).
      # Runner: vitest if a vitest.config.* sits above the test files, else jest (repo root).
      first=$(echo $TEST_FILES | awk "{print \$1}"); d=$(dirname "$first"); cfg=""
      while [ "$d" != "." ] && [ "$d" != "/" ] && [ -z "$cfg" ]; do
        for c in "$d"/vitest.config.*; do [ -f "$c" ] && cfg="$c"; done; d=$(dirname "$d")
      done
      if [ -n "$cfg" ]; then
        pkg=$(dirname "$cfg"); while [ ! -f "$pkg/package.json" ] && [ "$pkg" != "." ]; do pkg=$(dirname "$pkg"); done
        rel=""; for f in $TEST_FILES; do rel="$rel ${f#$pkg/}"; done
        (cd "$pkg" && CI=true npx vitest run --config "${cfg#$pkg/}" $rel --reporter=json --outputFile=/out/jest.json) >/out/jest.log 2>&1 || true
      else
        npx jest --ci --json --outputFile=/out/jest.json $TEST_FILES >/out/jest.log 2>&1 || true
      fi
      node -e "
        const r = require(\"/out/jest.json\"), path = require(\"path\");
        let p = 0, f = 0;
        for (const file of r.testResults) {
          const rel = path.relative(\"/app\", file.name);
          if (!file.assertionResults.length && file.status === \"failed\") console.log(\"ERROR \" + rel + \" \" + (file.message || \"\").split(\"\\n\")[0]);
          for (const a of file.assertionResults) {
            const ok = a.status === \"passed\"; ok ? p++ : f++;
            console.log((ok ? \"PASSED \" : a.status === \"pending\" ? \"SKIPPED \" : \"FAILED \") + rel + \" | \" + a.fullName);
          }
        }
        console.log(\"===== \" + f + \" failed, \" + p + \" passed =====\");
      " >/out/pytest.log 2>>/out/jest.log || { echo "jest produced no results:"; tail -40 /out/jest.log; } >/out/pytest.log
    else
      python -m pytest -rA -p no:cacheprovider $TEST_FILES >/out/pytest.log 2>&1 || true
    fi
  ' || { code=$?; echo "unresolved (container exit $code)"; exit 1; }

sync "$OUT/pytest.log" 2>/dev/null || true
if [[ -n "$PATCH" ]]; then MODE=patched; else MODE=base; fi
cp "$OUT/pytest.log" "$KEEP/$MODE.pytest.log"   # latest log per mode, read by scripts/derive_tests.py

python3 - "$MYTEST" "$IID" "$OUT/pytest.log" <<'EOF'
import json, os, re, sys
mytest, iid, log = sys.argv[1:]
def lst(v):
    while isinstance(v, str):
        v = json.loads(v)
    return v
tests = json.load(open(os.path.join(mytest, "task_test.json")))
if iid not in tests:
    tests = json.load(open(os.path.join(mytest, "candidates", "task_test.json")))
t = tests[iid]
f2p, p2p = lst(t["fail_to_pass"]), lst(t["pass_to_pass"])
status = {}
for line in open(log, errors="replace"):
    # SUBFAILED(...) lines come from subtests: the test may still be reported PASSED
    # on its own line, but it only counts as passing if none of its subtests failed.
    m = re.match(r"^(SUB)?(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)(\([^)]*\))? (.+?)(?: - .*)?$", line.rstrip("\n"))
    if not m:
        continue
    name, st = m.group(4), m.group(2)
    if status.get(name) in ("FAILED", "ERROR"):
        continue
    if m.group(1) and st == "PASSED":
        status.setdefault(name, "PASSED")
    else:
        status[name] = st
summary = [l.rstrip() for l in open(log, errors="replace") if re.match(r"^=+ .*(passed|failed|error).* =+$", l)]
print("== pytest:", summary[-1] if summary else "(no summary line; see log below)")
bad_f2p = [n for n in f2p if status.get(n) != "PASSED"]
bad_p2p = [n for n in p2p if status.get(n) != "PASSED"]
print(f"== fail_to_pass: {len(f2p) - len(bad_f2p)}/{len(f2p)} passing")
for n in f2p:
    print(f"   {status.get(n, 'MISSING'):8s} {n}")
print(f"== pass_to_pass: {len(p2p) - len(bad_p2p)}/{len(p2p)} passing")
for n in bad_p2p:
    print(f"   {status.get(n, 'MISSING'):8s} {n}")
if not summary:
    print(open(log, errors="replace").read()[-3000:])
print("RESOLVED" if not bad_f2p and not bad_p2p else "unresolved")
EOF
