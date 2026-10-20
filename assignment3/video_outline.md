# A3 video outline (3 min) — speaker cues, not a script

The video presents the same lessons as the write-up "in your own voice", so these are cues and facts
to talk from, not sentences to read. Each segment says what to have on screen (click the links to open
the code at that line) and the points to make. Timings add up to 3:00.

---

## 0:00–0:20 — Results first

**On screen:** the leaderboard's Past submissions page (or the table in [writeup_notes.md](writeup_notes.md)).

Points to make:
- five submissions: 8 → 10 → 7 → **10** solved, then a lean run
- best: 10/26 at $0.32 and 19:11 per solved task (rank #60)
- the story: accuracy came from parallel candidates; time and cost came from cutting wasted work

---

## 0:20–1:05 — How the harness works (main path)

**On screen, in this order:**

1. [agentic_loop.py:276](starter_code/src/agentic_loop.py:276) — `solve`: one task end to end
2. [agentic_loop.py:388](starter_code/src/agentic_loop.py:388) and [tools.py:434](starter_code/src/tools.py:434) — second candidate in its own git worktree under `/work`
3. [tools.py:142](starter_code/src/tools.py:142) — paths under `/app` redirected so candidates never touch each other's files
4. [agentic_loop.py:299](starter_code/src/agentic_loop.py:299) — both candidates run in parallel threads
5. [agentic_loop.py:325](starter_code/src/agentic_loop.py:325)–[338](starter_code/src/agentic_loop.py:338) — each patch graded as soon as its candidate finishes; the first pass stops the other
6. [agentic_loop.py:67](starter_code/src/agentic_loop.py:67) — retry only near misses (≤2 or ≤10 % failing, or tests did not run)

Points to make:
- 5 tasks open at once (the dispatcher maximum), 2 candidates each
- why near misses only: ~85 % of run time went to tasks that failed anyway; retries only ever converted 1–2-test misses (tasks 6 and 11 in the best run)

---

## 1:05–1:50 — Code instead of model turns

**On screen:**

1. [tools.py:508](starter_code/src/tools.py:508) — reconnaissance: harness greps the spec's names and finds nearby tests before the first model call
2. [agentic_loop.py:454](starter_code/src/agentic_loop.py:454) → [tools.py:391](starter_code/src/tools.py:391) — build gate: compile changed Go packages, check Python for syntax errors and new undefined names
3. [tools.py:619](starter_code/src/tools.py:619) — interface check: every `Name:` in the spec must exist in its `Path:`
4. [tools.py:225](starter_code/src/tools.py:225) and [tools.py:275](starter_code/src/tools.py:275) — edit tool from A2: closest match on a miss
5. [tools.py:15](starter_code/src/tools.py:15) — git guard: no commits/stash/reset that would empty the patch

Points to make:
- "reach for an LLM only when a step needs reasoning" — these replaced model turns with code
- numbers: failed edits 36 % → ~1 %; gate caught `NameError` / broken Go builds before grading
- the interface check targets task 2, "tests did not run" in every run

---

## 1:50–2:35 — Pitfalls

**On screen:**

1. [agentic_loop.py:209](starter_code/src/agentic_loop.py:209) and [agentic_loop.py:691](starter_code/src/agentic_loop.py:691) — API cap and 429 handling
2. [tools.py:415](starter_code/src/tools.py:415) — the comment about not compiling `_test.go` files
3. [config.py](starter_code/src/config.py) — `MAX_TURNS`, `TASK_WALL_S`, `NEAR_MISS_MAX_FAILED`, `MAX_CONCURRENT_CALLS`

Points to make (pick 3):
- **429s:** API allows 8 requests in flight; 10 candidate loops exceeded it and a candidate died → global cap of 7
- **gate false alarm:** spec changed a signature, old tests still used the old one → compile only non-test code
- **limits too tight:** 60 turns / 25 min → 7/26; candidates ran out of time while waiting for API slots
- **dev-set overfitting:** 5/5 on the 5 CloudLab tasks, 10/26 on the leaderboard
- **one-shot Expert experiment:** SEARCH blocks did not match the real code, every one-shot patch failed → turned off

---

## 2:35–3:00 — What helped most, what next

**On screen:** [writeup_notes.md](writeup_notes.md) (results table) or the leaderboard.

Points to make:
- helped most: parallel candidates + early stop; near-miss-only retries; A2 edit tool; API cap
- next: spec-derived tests (the grader only returns failure counts); one end-to-end time budget per task instead of per-phase limits that add up
- the lean submission tested the other end: fewer model calls, faster and cheaper

---

### Recording tips

- open the files in the editor beforehand, in the order above, so each click is one jump
- zoom the editor (~150 %) so the code is readable in the recording
- keep the face camera visible in a corner the whole time
