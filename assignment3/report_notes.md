# Notes for the report's three questions (facts to write from, not the report)

Write the report in your own words. Numbers come from [writeup_notes.md](writeup_notes.md) and
[config.py](starter_code/src/config.py); links point at the committed `src/` (the `src_skip` version).

Best submission: **run 5 — 10/26 solved, $0.317 per solve, 19:11 per solve, rank #60.**

---

## 1. Main components of the harness

**Scheduling: 5 tasks at once**
- 5 worker threads, one per open task, which is the most the dispatcher allows
  ([agentic_loop.py:223](starter_code/src/agentic_loop.py:223), [:233](starter_code/src/agentic_loop.py:233)).
- One run-wide clock: after 3 h 40 m, open no new tasks and wrap up the open ones (the hard limit is 4 h).

**Per-task pipeline** ([agentic_loop.py:276](starter_code/src/agentic_loop.py:276) `solve`)
1. Recon is a script, not model turns. It greps the spec's names, finds nearby tests and the test
   command, and puts all of that in the first message ([tools.py:508](starter_code/src/tools.py:508)).
2. **2 parallel candidates**, i.e. independent attempts. Candidate 1 works in its own git worktree under `/work`,
   and its `/app` paths are rewritten to point there ([agentic_loop.py:392](starter_code/src/agentic_loop.py:392),
   [tools.py:434](starter_code/src/tools.py:434), [tools.py:142](starter_code/src/tools.py:142)).
3. Each candidate runs a tool loop: bash / view_file / str_replace / create_file / finish
   ([agentic_loop.py:535](starter_code/src/agentic_loop.py:535)). Limit: 70 turns or 30 min.
4. **Build gate** before every grading: compile the changed Go packages, check Python syntax and new
   undefined names, and check that every name the interface declares exists in its file. The errors go back to the model
   (2 rounds of 8 turns) ([agentic_loop.py:458](starter_code/src/agentic_loop.py:458), [tools.py:391](starter_code/src/tools.py:391)).
5. **Expert review**: one Expert call compares the diff with the spec ([agentic_loop.py:487](starter_code/src/agentic_loop.py:487)).
6. Each candidate is graded as soon as it finishes, and the first passing patch stops the others
   ([agentic_loop.py:329](starter_code/src/agentic_loop.py:329)–[342](starter_code/src/agentic_loop.py:342)).
7. Near-miss retries (see Q2), then the best patch is regraded if the last grading scored worse,
   because the last evaluation is the one that counts ([agentic_loop.py:384](starter_code/src/agentic_loop.py:384)).

**Memory carried between tasks: the playbook**
- After each task, the Expert rewrites a list of at most 10 lessons (≤2,500 chars), and that list goes into later
  tasks' system prompts ([agentic_loop.py:650](starter_code/src/agentic_loop.py:650)).
- It isn't updated in the last 15 minutes of the run. If another task holds the lock, the report is queued so no task waits.

**Memory within a task: context compaction**
- Once the conversation passes 160k chars, older turns are summarized and the last 14 messages kept
  ([agentic_loop.py:599](starter_code/src/agentic_loop.py:599)).

**Which model tier does what**
| tier | used for |
|---|---|
| Standard | every agent turn (first attempt, retries, gate fixes, review fixes) and compaction summaries |
| Expert | review of the diff (1 call per candidate) and playbook rewrites only |
| Starter | not used in the best run (tried in the "cheap" configuration) |

**Tools carried over from A2**
- The edit tool falls back to the closest match on a miss, strips line numbers and shows the edited region
  ([tools.py:225](starter_code/src/tools.py:225), [:275](starter_code/src/tools.py:275)).
- A git guard refuses commit/stash/reset and restores HEAD before taking the diff
  ([tools.py:15](starter_code/src/tools.py:15), [:302](starter_code/src/tools.py:302)).

**Infrastructure**
- At most 7 API calls in flight across all tasks (the account limit is 8), and 429s are waited out
  ([agentic_loop.py:209](starter_code/src/agentic_loop.py:209), [:695](starter_code/src/agentic_loop.py:695)).
- A cost ledger logs tokens, cached tokens and estimated $ per task ([agentic_loop.py:430](starter_code/src/agentic_loop.py:430)).

---

## 2. Optimizations and trade-offs

**When to keep working on a failing task and when to give up ("fail fast")**
- Retry only on a **near miss**: ≤2 hidden tests fail, ≤10 % fail, or the tests didn't run
  ([agentic_loop.py:67](starter_code/src/agentic_loop.py:67) `_worth_retry`). Otherwise stop.
- Retries are capped at 3 attempts, each with 25 turns and half the time, and only when ≥50 min of run time is left.
- Why: in run 4, about 85 % of task-minutes went to tasks that failed anyway (about 700 of 830).
  Retries only ever turned 1–2-test misses into solves (tasks 6 and 11 in run 5).
- Result: run 5 had the same 10 solves as run 3, but cost dropped from $0.349 to $0.317 per solve
  and time from 21:37 to 19:11.
- The trade-off: a task that would pass after a bigger rework is given up on.

**How many tasks are open at once**
- 5, the dispatcher's maximum, so slots never sit idle.
- Trade-off: 5 tasks × 2 candidates compete for 7 API slots, so calls queue. That queueing made tasks slower
  and lowered the prompt-cache hit rate from 90–97 % to 70–87 %.

**2 candidates per task**
- Trade-off: more solves (variance) against about 2× the cost per task.
- CloudLab went from 1–2/5 to 3/5; the leaderboard went from 8 to 10 solved.
- Grading each candidate as it finishes, with an early stop, takes back much of the time cost:
  tasks finished in 11–31 min instead of 39–56.
- Near the end of the run (<30 min left), a task gets only 1 candidate.

**Turn and time limits**
- 70 turns / 30 min was the best setting. Tighter limits (60 / 25) got **7/26**: 6 of 10 candidates
  hit the turn limit before finishing.

**Which model where**
- The Standard tier does the work. The Expert tier's output tokens cost about 36× Standard's, so it only does
  cheap, high-leverage calls (one review, about 10–15k input tokens; playbook updates). Using the Expert for all
  turns would have cost about $4 per task.

**Deterministic code instead of model turns**
- Recon, the build gate and the interface check are scripts. They spend no tokens and catch errors before
  each grading (gradings are limited and slow).

**Skipping tasks** (final version, allowed by staff)
- The 12 tasks no version solved in 4 runs are closed right away ([config.py:18](starter_code/src/config.py:18),
  [agentic_loop.py:279](starter_code/src/agentic_loop.py:279)). This saves time on certain failures, but it can't add solves.
  Result: ___ (fill in from tonight's run; it was on pace to stay below 10).

**Other configurations tried** (fill in the results from the leaderboard)
- lean (1 candidate, 35 turns, 12 min, no review/retries/playbook): ___
- cheap (relies mostly on the Starter tier): ___
- medium (1 candidate, 70 turns, 20 min, near-miss retries): ___

---

## 3. Pitfalls, and what helped

**Pitfalls**
| pitfall | what happened |
|---|---|
| Patches that didn't compile | NameError (`key_int`), Go build errors were graded and failed outright (run 2) |
| Build gate false alarm | the spec changed a signature, old tests still called the old one (vuls `isRunningKernel`), ~48 turns wasted → gate now compiles non-test code only |
| Exploring without editing | a task used 60 turns and produced no patch; 2 leaderboard tasks ended with "no patch" |
| Edit tool failures | 36 % of str_replace calls failed (24/67) |
| 429 rate limit | "limit: 8" concurrent requests killed a candidate after ~8 min of retries |
| Waiting for the slow candidate | one candidate was done early but waited for the other's 40-min limit |
| Limits too tight | 60 turns / 25 min → 7/26 |
| Per-phase time limits add up | 30 min first attempt + 2×5 gate + 6 review + 2×15 retries → some tasks took over 1 h |
| Dev-set overfitting | 5/5 on the 5 CloudLab tasks with the same version that scored 10/26 on the leaderboard |
| Small-sample variance | the same version scored 1–3/5 on the same 5 tasks |
| One-shot Expert patch (negative result) | edit blocks failed to match (3/7, 8/24), builds broke, every one-shot patch failed all tests |
| Setup | a wrong API key (login password exported) → every call 401; `agent.log` is only written at the end of a run |

**What helped, by metric**
- **Tasks solved:** 2 parallel candidates (8 → 10); the build gate (no more uncompiled patches graded);
  the A2 edit tool (failure rate 36 % → about 1 %, 1/84); the no-edit nudge at turns 20/40; near-miss retries
  (tasks 6 and 11).
- **Cost per solve:** fail-fast retries ($0.349 → $0.317); prompt caching (90–97 % of input tokens);
  the Expert tier kept to 1 review per candidate; skipping identical regrades.
- **Time per solve:** grading as each candidate finishes plus the early stop (39–56 → 11–31 min per task);
  fail-fast retries (21:37 → 19:11); env hints (Go offline modules) instead of ~25 turns spent searching the module cache.
- **Didn't help:** tighter limits (7/26) and the one-shot Expert patch (all failed).

**What you'd do next** (optional closing)
- One end-to-end time budget per task instead of a limit per phase.
- Write tests from the spec for "new tests failed" cases.
- Time per solve was the weak axis: the top 20 had 2–13 min per solve against your 19 min.
