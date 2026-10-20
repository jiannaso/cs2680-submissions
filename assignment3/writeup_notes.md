# A3 reference notes (facts for the write-up and video, prepared by Claude; not the write-up)

The write-up must be your own words. These are only the facts, numbers and file locations behind it.
Links open `starter_code/src/` at the line; line numbers are for the version committed (the `src_iface.zip` version: fail-fast + interface check).

## Leaderboard submissions

| # | when | version | solved | $/solved | time/solved | total time (≈) | rank / note |
|---|---|---|---|---|---|---|---|
| 2 | Oct 19, 6:20 PM | single loop, 60 turns + 25-turn retry, no gate | 8 | $0.159 | 21:16 | 2 h 50 | |
| 3 | Oct 19, 11:59 PM | 2 parallel candidates (git worktrees), gate, nudge, review, playbook | 10 | $0.349 | 21:37 | 3 h 36 | |
| 4 | Oct 20, 9:48 AM | + A2 edit tool, git guard, API cap, recon, budget note, tighter limits (60 turns / 25 min) | 7 | $0.406 | 25:00 | 2 h 55 | limits too tight |
| 5 | Oct 20, 2:54 PM | fail-fast: near-miss-only retries, 70 turns / 30 min, budget note off | **10** | **$0.317** | **19:11** | 3 h 16 | **Best, rank #60** |
| 6 | Oct 20, evening | lean: 1 candidate, 35 turns, 12 min, no review / retries / playbook | (pending) | | | | aimed at the faster part of the skyline |

- Tasks solved at least once across runs 2–5: 3, 5, 6, 7, 8, 11, 12, 13, 14, 17, 18, 19, 20, 26 (14 tasks); solved in every run: 7, 18, 19, 20, 26.
- Never solved in any run: 1, 2, 4, 9, 10, 15, 16, 21, 22, 23, 24, 25 (12 tasks).
- Task 2: "tests did not run" in all four runs → motivated the interface completeness check.
- Run 5 vs run 3: same 10 solves, lower cost and time → dominates it.
- Top 20 of the leaderboard: 11–20 solves at 2–13 min/solve; time per solve was the weak axis.
- Another student: 9 solves, $0.062, 6:47 → rank 20 (fast/cheap region is crowded).

## Harness components (where they live)

| component | file:line | what it does |
|---|---|---|
| worker threads, 5 tasks live | [agentic_loop.py:223](starter_code/src/agentic_loop.py:223) (`run`), [:233](starter_code/src/agentic_loop.py:233) (`_worker`) | NUM_WORKERS = 5 = dispatcher maximum |
| per-task flow | [agentic_loop.py:276](starter_code/src/agentic_loop.py:276) (`solve`) | recon → candidates → grading → retries → regrade best |
| parallel candidates | [agentic_loop.py:299](starter_code/src/agentic_loop.py:299), [:388](starter_code/src/agentic_loop.py:388) (`_extra_candidates`) | 2 candidates; candidate 1 in a git worktree under /work ([tools.py:434](starter_code/src/tools.py:434)) |
| path redirect for candidate 1 | [tools.py:142](starter_code/src/tools.py:142) (`_remap_command`) | `/app/...` paths rewritten to its own copy |
| grade as each candidate finishes, early stop | [agentic_loop.py:325](starter_code/src/agentic_loop.py:325), [:338](starter_code/src/agentic_loop.py:338) | first passing patch stops the others |
| near-miss retry policy | [agentic_loop.py:67](starter_code/src/agentic_loop.py:67) (`_worth_retry`), [:351](starter_code/src/agentic_loop.py:351) | retry only if ≤2 or ≤10 % tests fail, or tests did not run |
| regrade best | [agentic_loop.py:380](starter_code/src/agentic_loop.py:380) | last evaluation counts, so the best patch is regraded if needed |
| tool loop | [agentic_loop.py:531](starter_code/src/agentic_loop.py:531) (`_agent_loop`) | bash / view_file / str_replace / create_file / finish |
| no-edit nudge, time warning | [agentic_loop.py:516](starter_code/src/agentic_loop.py:516), [prompts.py:134](starter_code/src/prompts.py:134), [:177](starter_code/src/prompts.py:177) | nudge at turns 20/40 if nothing changed; warn at 80 % of time |
| build/lint gate + interface check | [agentic_loop.py:454](starter_code/src/agentic_loop.py:454), [tools.py:391](starter_code/src/tools.py:391), [:332](starter_code/src/tools.py:332), [:619](starter_code/src/tools.py:619) | go build of changed pkgs; Python syntax + new undefined names; interface names present |
| expert review | [agentic_loop.py:483](starter_code/src/agentic_loop.py:483), [prompts.py:139](starter_code/src/prompts.py:139) | one Expert call on the diff vs spec |
| context compaction | [agentic_loop.py:595](starter_code/src/agentic_loop.py:595) | summarize at 160k chars, keep last 14 messages |
| playbook across tasks | [agentic_loop.py:646](starter_code/src/agentic_loop.py:646) | Expert rewrites ≤10 lessons after each task |
| API concurrency cap, 429 handling | [agentic_loop.py:209](starter_code/src/agentic_loop.py:209), [:691](starter_code/src/agentic_loop.py:691) | ≤7 calls in flight (account limit 8); 429s waited out |
| cost ledger | [agentic_loop.py:426](starter_code/src/agentic_loop.py:426) | per-task tokens, cached tokens, $ estimate in agent.log |
| recon (scripted search) | [tools.py:508](starter_code/src/tools.py:508), [:485](starter_code/src/tools.py:485) | grep spec names, nearby tests, test command → first message |
| edit tool (from A2) | [tools.py:225](starter_code/src/tools.py:225), [:275](starter_code/src/tools.py:275) | closest-match on miss, strip line numbers, show edited region |
| git guard (from A2) | [tools.py:15](starter_code/src/tools.py:15), [:302](starter_code/src/tools.py:302) | refuse commit/stash/reset…; restore HEAD before diff |
| model tiers | [config.py](starter_code/src/config.py) | Standard: all agent turns; Expert: review + playbook only |

## Changes, the problem behind each, and the evidence

| change | problem it addressed | evidence |
|---|---|---|
| build gate | patches that did not compile / NameError (`key_int` in task 0, Go build errors in task 1) | run 2 traces |
| gate compiles non-test code only | false alarm: spec changed a signature, old tests still used it (vuls `isRunningKernel`), ~48 turns wasted | CloudLab run 3 log |
| no-edit nudge | task 4 explored 60 turns, empty patch; 2 leaderboard tasks "no patch" at 60 turns | run 2 trace; leaderboard run 2 |
| skip identical regrade | task 2 graded the same 4680-byte patch twice | run 2 sequence.log |
| env hints (Go offline modules) | task 1 spent ~25 turns grepping the module cache | run 2 trace |
| 2 parallel candidates | single-attempt variance | CloudLab 1–2/5 → 3/5; leaderboard 8 → 10 |
| A2 edit tool | 36 % of str_replace calls failed (24/67; task 3: 14/28) | → ~1 % (1/84) next run |
| API concurrency cap | 429 "Too many concurrent requests (limit: 8)" killed a candidate (api error after ~8 min of retries) | CloudLab run 6 log |
| grade as finished + early stop | task 4: c0 done early but waited for c1's 40-min time limit | CloudLab run 5 log; next run tasks 11–31 min vs 39–56 |
| tighter limits (60 turns / 25 min) | — (tried for speed) | leaderboard 7/26; 6/10 candidates ended on turn limit; tasks with 56–66 turns took 22–48 min (time-limited by slot waits) |
| fail-fast retries | ~85 % of task-minutes went to tasks that failed (19 failed tasks ≈ 700 of ≈ 830 task-min in run 4) | → run 5: 10 solved, 19:11/solve; tasks 6 and 11 solved after near-miss retries |
| interface check | task 2 "tests did not run" every run | parsing + real repos (openlibrary, flipt) verified; not yet in a leaderboard run |
| one-shot Expert candidate (tried, turned off) | idea: few calls instead of 60+ turns | CloudLab: blocks failed to match (3/7, 8/24), builds broken, every one-shot patch failed all tests |
| Expert retries (not done) | cost: Expert output 36× Standard; ~$4/task if used throughout | estimate from task 0 token counts |

## Cost and caching

- Prompt cache hit: 90–97 % of input tokens (Standard); dropped to 70–87 % once calls waited for slots.
- Standard cost per task: $0.03–0.15; full runs $1.3–4.
- Expert reviewer: ~10k input tokens/task, mostly answered LGTM (8 of 9 in one CloudLab run).

## Pitfalls worth mentioning

- Dev-set overfitting: the 5 CloudLab tasks scored 5/5 with the same version that got 10/26 on the leaderboard; several fixes came from those 5 tasks.
- Small-sample variance: same version scored 1–3/5 on the same 5 tasks across runs.
- Separate per-phase time limits add up (first attempt 30 min + gate 2×5 + review 6 + retries 2×15): some tasks ran > 1 h.
- Wrong API key (login password exported as the key) → every call 401 in the first CloudLab run.
- `agent.log` is only written at the end of a run (`docker logs` while running).

## Video outline (3 min)

1. (0:00–0:20) Result arc: 8 → 10 → 7 → 10 solved; best 10 at $0.32 and 19:11 per solve (rank #60).
2. (0:20–1:10) Architecture: open [agentic_loop.py:276](starter_code/src/agentic_loop.py:276) `solve` → candidates [:299](starter_code/src/agentic_loop.py:299) and worktree [tools.py:434](starter_code/src/tools.py:434) → grade as finished / early stop [agentic_loop.py:325–338](starter_code/src/agentic_loop.py:325) → near-miss retries [agentic_loop.py:67](starter_code/src/agentic_loop.py:67).
3. (1:10–1:50) Deterministic pieces instead of model turns: recon [tools.py:508](starter_code/src/tools.py:508), gate [tools.py:391](starter_code/src/tools.py:391) + interface check [:619](starter_code/src/tools.py:619), edit tool [:225](starter_code/src/tools.py:225) / [:275](starter_code/src/tools.py:275), git guard [:15](starter_code/src/tools.py:15).
4. (1:50–2:30) Pitfalls: gate false alarm (vuls), 429 limit → cap [agentic_loop.py:209](starter_code/src/agentic_loop.py:209), tight limits (7/26), one-shot negative result.
5. (2:30–3:00) What helped most and what you would do next: fail-fast retries; spec-derived tests for "new tests failed"; one end-to-end time budget per task.
