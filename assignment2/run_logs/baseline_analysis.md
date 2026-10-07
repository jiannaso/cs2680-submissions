# Baseline run (run_all.sh --workers 3): 4/10 resolved

Evidence notes for the write-up. Hidden test sources were read with
`git show <fix-commit>:<test file>` inside each task image and saved next to
this file (run_logs/*_test*.py / .go). No pass_to_pass test regressed in any task.

| task | result | F2P | likely cause | solvable? |
|---|---|---|---|---|
| ansible-f327 | PASS | | | |
| qutebrowser-c580 | PASS | | | |
| flipt-2eac | PASS | | | |
| qutebrowser-de4a | PASS | | | |
| openlibrary-4a5d | FAIL | 0/1 | agent ignored the data shape the requirements spell out | yes (agent) |
| protonmail-944a | FAIL | 0/9 | named export only; test does a default import; also re-exported an undefined type | yes (agent) |
| ansible-a26c | FAIL | 3/4 | `use_netrc` forwarded to `Request()` but not to `.open()` | probably (agent) |
| qutebrowser-21b4 | FAIL | 2/3 | exact `__repr__` string not derivable from the task | candidate unsolvable |
| ansible-83909 | FAIL | 0/1 | exact error message not given; test's text contradicts the requirement | candidate unsolvable |
| teleport-89f0 | FAIL | 0/1 | test contradicts requirement 3 | candidate unsolvable |

## Details

### teleport-89f0 (TestReadAtMost)
Test case `{5, "hello", ErrLimitReached}`: reading "hello" with limit 5 must return
all 5 bytes AND `ErrLimitReached`. Requirement 3: "When the limit allows reading
all available content, `ReadAtMost` must return all bytes without error."
Requirement 2 only asks for the error when the limit is reached "before
completing the content". A limit of 5 allows reading all of "hello", so a
spec-following implementation returns nil and fails. The agent did exactly that
(actual: `<nil>`).

### ansible-83909 (test_api_no_auth_but_required)
Test: `pytest.raises(AnsibleError, match="No access token or username set. A token can be set with --api-key or at ")`.
Requirement: "update the error message in the Galaxy API to indicate the new
authentication options via token file or --token parameter". The exact wording
is never given, and the required text mentions `--api-key ... or at <file>`,
while the requirement says `--token`. The agent left the message as
"... --api-key or --token, or set in ansible.cfg.", which matches the
requirement's wording but not the regex.

### qutebrowser-21b4 (test_repr)
Expected repr: `Values(opt=..., vmap=odict_values([ScopedValue(...), ...]))`, i.e.
`utils.get_repr(self, opt=..., vmap=self._vmap.values(), constructor=True)`.
Requirement only says `__repr__` "should be updated to use the new internal
`_vmap` structure". The agent produced `values=self._vmap` (key `values`, an
OrderedDict). Both the key name `vmap` and the `.values()` view are unstated.
Gray area: renaming the key after the attribute is guessable, `.values()` less so.

### ansible-a26c (test_open_url)
Test asserts `Request.open` is called with `..., ciphers=None, use_netrc=True)`.
The agent wrote `Request(use_netrc=use_netrc).open(...)` without passing
`use_netrc` to `.open()`. The requirement says open_url should "forward the value
when creating a Request", which the agent read literally. Every other open_url
parameter is forwarded to `.open()`, and `Request.open` gains the parameter too,
so following the existing pattern would pass. Agent-side.

### openlibrary-4a5d (test_get_statement_values)
Test data: `{'P2038': [{'value': {'content': 'Chris-Wiggins'}}]}`. The requirement
says to "collect the string in `value.content`". The agent instead navigated
`mainsnak.datavalue.value.content` (the real Wikidata JSON shape) and got [].
Only 10 iterations; its repro check was built around its own assumed shape, so
it passed. Agent-side.

### protonmail-944a (observeApiError, 9 tests)
TypeScript compile error: `TS2613: Module '.../lib/observeApiError' has no
default export` (the test does `import observeApiError from '../lib/observeApiError'`).
The interface only says the file "declares and exports" the function. The agent
also re-exported `MetricsApiStatusTypes` from index.ts without defining it.
Exporting both default and named, and type-checking, would pass. Agent-side.
