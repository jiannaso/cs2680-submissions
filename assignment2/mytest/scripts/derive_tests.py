"""Fill fail_to_pass / pass_to_pass in mytest/task_test.json from two verify.sh runs.

usage: python3 mytest/scripts/derive_tests.py INSTANCE_ID

Needs mytest/results/.verify/<id>/base.pytest.log (verify.sh without a patch) and
.../patched.pytest.log (verify.sh with gold.diff). fail_to_pass = tests that fail at
base_commit and pass with gold.diff; pass_to_pass = tests that pass in both.
"""
import json
import os
import re
import sys

MYTEST = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def parse(path):
    status = {}
    for line in open(path, errors="replace"):
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
    return status


def main(iid):
    d = os.path.join(MYTEST, "results", ".verify", iid)
    base, gold = parse(os.path.join(d, "base.pytest.log")), parse(os.path.join(d, "patched.pytest.log"))
    f2p = sorted(n for n, s in gold.items() if s == "PASSED" and base.get(n) != "PASSED")
    p2p = sorted(n for n, s in gold.items() if s == "PASSED" and base.get(n) == "PASSED")
    broken = sorted(n for n, s in gold.items() if s != "PASSED" and s != "SKIPPED")
    path = os.path.join(MYTEST, "task_test.json")
    tests = json.load(open(path))
    tests[iid]["fail_to_pass"] = json.dumps(f2p)
    tests[iid]["pass_to_pass"] = json.dumps(p2p)
    json.dump(tests, open(path, "w"), indent=2)
    print(f"{iid}: fail_to_pass={len(f2p)} pass_to_pass={len(p2p)} not passing with gold={len(broken)}")
    for n in f2p:
        print("   F2P", n)
    for n in broken:
        print("   gold does not pass:", n)


if __name__ == "__main__":
    main(sys.argv[1])
