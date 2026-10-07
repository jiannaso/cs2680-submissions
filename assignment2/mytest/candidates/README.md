# mytest candidates (evidence, not deliverables)

Every task built and tested while looking for `solved_after_change` tasks: a task the
earlier agent fails and the submitted agent passes, both reliably (2/2 runs each).
None met that bar, so `../solved_after_change.json` is empty. The two unsolved
tasks that were kept are in `../unsolved.json` / `../tasks/`.

## Layout

| path | content |
|---|---|
| `candidates.json` | agent-side task entries (same shape as `agent_task_input.json`) with the problem statements |
| `task_test.json` | tests per candidate (`before_repo_set_cmd`, `selected_test_files_to_run`, `fail_to_pass`, `pass_to_pass`) |
| `tasks/<id>/` | `Dockerfile`, `gold.diff` (upstream fix), `test.diff` (held-out tests) per candidate |
| `runs/harness_v1/`, `runs/harness_before/` | patches and agent logs produced by the frozen agents in `../harness_v1/` and `../harness_before/` |
| `results/*.txt` | per-run scores (`verify.sh` output), in the order the rounds were run |

`../verify.sh` also finds candidates here, so any of them can be re-scored:
build `tasks/<id>/Dockerfile` with the tag in `candidates.json`, then
`bash mytest/verify.sh <id> <patch>`.

## Agent versions in the result files

| name in results | agent | given-task score |
|---|---|---|
| `v1`, `harness_v1` | `../harness_v1/`, the agent of the first full run | 4/10 |
| `v2` | intermediate (prompt rules + final review) | 5/10 |
| `final` (rounds A, B, r1-r2, d3-d4, s1-s6) | the agent at that time (between 5/10 and 7/10) | - |
| `harness_before`, current-agent rounds (Q1/Q2, BO1, U1/U2, x1/x2 "before") | `../harness_before/` = the submitted agent | 7/10 |
| `after` in x1/x2, QA1/QA2, PA1/PA2 | experimental changes on top of the 7/10 agent; none helped, all were reverted | - |

## Known issues in the raw result files

- Early rounds (01-04, part of 05/06) were scored by an older `verify.sh`: a race
  could feed a stale patch to the container ("container exit 4"), and a parser bug
  missed pytest ids containing spaces. `09_rescored_with_fixed_verify.txt` re-scores
  the affected patches with the fixed script; conclusions did not change.
- `BO2` (boltons-513, 2026-10-07 16:23) is invalid: the course API budget ran out
  during that run (429 "Budget for Assignment 2 exhausted"); the agent never finished.
- Some problem statements were revised after early runs to be fair to the hidden
  tests (e.g. dotenv-615 path type, dotenv-680 escaping, boltons-513 memoryview
  bytes); results before and after a revision are not comparable.
