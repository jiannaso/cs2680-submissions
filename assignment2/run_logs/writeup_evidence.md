# Write-up evidence notes (raw facts only, not write-up text)

Write the PDF in your own words. Everything below is a pointer to evidence.

---------------------------------------------------------------------------
## Section 1: unsolvable given tasks (20%)

Score history, all four full runs (baseline 4/10, run2 5/10, run3 6/10, run4 7/10):
the three below failed in every run, with the same failing test each time.
No pass_to_pass regression in any of them.

### gravitational__teleport-89f0432...
- failing test: TestReadAtMost (lib/utils/utils_test.go:550-567, saved: run_logs/teleport_utils_test.go)
- test cases: {4,"hell",ErrLimitReached}, {5,"hello",ErrLimitReached}, {6,"hello",nil}; reader = "hello"
- requirement 2: error "when the read reaches the limit before completing the content"
- requirement 3: "When the limit allows reading all available content, ReadAtMost must return all bytes without error"
- conflict: limit 5 on "hello" reads all content, so req 3 says nil, but the test wants ErrLimitReached
- agent behaviour: implemented req 3, so actual = <nil> (pro_eval stdout: "expected read limit reached, actual <nil>")

### ansible__ansible-83909bfa...
- failing test: test/units/galaxy/test_api.py::test_api_no_auth_but_required (saved: run_logs/ansible83909_test_api.py:75-78)
- test regex: "No access token or username set. A token can be set with --api-key or at "
- requirement: "update the error message ... to indicate the new authentication options via token file or --token parameter"
- gaps: exact wording never given; the test needs "--api-key" + "or at <path>", while the requirement names "--token"
- agent output kept "...--api-key or --token, or set in ansible.cfg." (matches the requirement wording, not the regex)
- interface field: "No new interfaces are introduced"

### qutebrowser__qutebrowser-21b426b...
- failing test: tests/unit/config/test_configutils.py::test_repr (2 of 3 fail_to_pass pass every run)
- expected: "...Values(opt=..., vmap=odict_values([ScopedValue(...), ...]))", i.e. get_repr(..., vmap=self._vmap.values())
- requirement: "__repr__ ... should be updated to use the new internal _vmap structure instead of the old _values list"
- unstated: the keyword name `vmap` and the `.values()` view; agent produced values=self._vmap
- weaker case than the other two (renaming the keyword after the attribute is guessable); decide how you argue it


### What the agent tried (run4 logs: run_logs/run4/madsLoop_logs/<id>/)
- teleport (37 iterations): wrote ReadAtMost + ErrLimitReached (it 10-12); its repro check tested "read within limit", "exactly at limit" etc.; at it 20-21 it deliberately added a 1-byte peek read ("we've read exactly `limit` bytes but there may be more") so an exactly-at-limit read returns nil, which is requirement 3 implemented on purpose; went through final review, done at it 37. Upstream instead uses io.LimitedReader and returns ErrLimitReached whenever N <= 0 (exactly-at-limit included). Upstream code: git show 89f0432:lib/utils/utils.go lines 540-555.
- ansible-83909 (80 iterations): removed login.py, added an execute_login error with alternatives (it 33-34), added `--token` as an alias of `--api-key` (it 35, following the requirement), rewrote the api.py message as "...A token can be set with --api-key, ..." (it 36). Upstream message: "No access token or username set. A token can be set with --api-key or at {GALAXY_TOKEN_PATH}." (api.py:219). 149/149 other tests pass.
- qutebrowser-21b4 (37 iterations): converted the whole class to _vmap/OrderedDict (it 13-25), __repr__ as get_repr(self, opt=..., values=list(self._vmap.values()), ...) (run4); earlier runs: values=self._vmap. The keyword stayed `values=` in every run. Upstream: get_repr(self, opt=self.opt, vmap=self._vmap.values(), constructor=True) (configutils.py:96-98). The other 2 F2P (iter, add) and 25/25 P2P pass.

### For contrast (solvable, agent-side, fixed by harness changes)
- openlibrary: agent assumed Wikidata's mainsnak.datavalue nesting; the task says value.content; fixed by the assumption check (run4 pass)
- protonmail: named export vs the test's default import; fixed by the default-export check in done (run3 and run4 pass)
- ansible-a26c: use_netrc not forwarded to .open() (baseline), or default True instead of None (run3); fixed by the sibling-parameter rule

---------------------------------------------------------------------------
## Section 2: differences from the handout (10%), facts per change

Part 0
- Task input: -p gets problem_statement + requirements + interface, double-encoded JSON decoded (run_task.sh); handout passes problem_statement only
- Logging: run_end always written (finally block), SIGTERM -> SystemExit; reasons done/no_tool_calls/error/max_iterations/time_limit; tool_call logged before execution; schema checker scripts/check_logs.py
- Git safety: state-changing git commands refused in bash; start commit recorded and HEAD restored/unstaged at exit (src/git_guard.py)

Part 1: tools (src/tools.py)
- read_file: 400-line window + "continue at" note, lists directories (the handout's version returned the whole file, so 12k truncation cut out the middle)
- edit_file: keeps CRLF; strips copied line-number prefixes; closest-match hint on miss; Python syntax warning; shows the edited snippet; creates files; on new file shows how nearby tests import code
- bash: fresh-shell note, 300 s timeout, no stdin, API key removed from env
- repro_check: new tool (the handout's prompt refers to it, but its tool list doesn't define it)
- done: refuses once on empty diff; reruns repro checks; final review = task text + per-requirement checks + assumption audit; pushes back if done is repeated without any check; default-export check for new JS/TS modules; review skipped when budget low

Part 1: loop (src/agentic_loop.py)
- no tool call -> nudge (max 3 consecutive) instead of break; cut-off (finish_reason=length) nudge
- limits: 100 iterations, 50 min, wrap-up warning near the limit
- identical call repeated 3x -> note to change approach
- retries only network/408/429/5xx with backoff, logged as api_retry

Part 1: state (src/context.py, prompts.py)
- compaction: at >80k prompt tokens, old tool outputs stubbed in one batch (cache-friendly); emergency compaction on context error
- harness notes appended to the last tool result (log = what the model saw; earlier messages never rewritten)
- system prompt: requirements/interface binding; literal data shapes; sibling parameter defaults and forwarding; verify with project tooling; `git worktree` for baseline comparison (instead of stash)

Part 2
- docker_env.sh: openai==3.24.0 into /tmp/madsloop_deps with the harness's Python, pip bootstrapped privately (handout: pip install into the image's Python, which the repo's tests also use)
- run_task.sh: container sees a read-only copy of the harness only (no task_test.json / pro_eval / run_logs); separate writable log mount; patch collected even if the agent crashes

Measured effect: baseline 4/10 -> run2 5/10 -> run4 7/10 (run_logs/baseline_run1, run2, run3, run4)

---------------------------------------------------------------------------
## Section 3: mytest (10%), checklist status

| requirement | status |
|---|---|
| 4 tasks, none from SWE-bench Pro | unsolved: schedule-604, dotenv-680 (pending before/after experiment); solved_after_change: pending |
| single-paragraph problem_statement | yes (drafted by Claude: review/rewrite in your own words) |
| Dockerfile builds | yes (python:3.12-slim / node:14 + Python 3.12) |
| gold.diff turns fail_to_pass green, no pass_to_pass broken | verified with verify.sh for every candidate |
| runs under run_task.sh | yes (TASKS_JSON=mytest/<file>.json) |
| scores under verify.sh | yes (pytest + jest) |
| results/<id>.before.log and .after.log (8 files) | TODO once the 4 tasks are final |
| solved_after_change.json / unsolved.json split (2 + 2) | TODO (solved_after_change.json currently lists all candidates) |
| before fails / after passes, explanation matches code | pending experiment x1/x2 (before = mytest/harness_before) |
