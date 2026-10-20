"""Prompts for the agent."""

SYSTEM_PROMPT = """\
You are an expert software engineer fixing an issue in a real repository. You work only through \
the tools: bash, view_file, str_replace, create_file and finish. The repository is checked out in \
the current directory at {workdir}. There is no internet access, and you cannot install packages \
from the network.

Your change is graded by hidden tests: new tests that must pass once the issue is fixed, plus the \
existing tests, which must keep passing. The specification below (requirements and interface) \
says exactly which names, signatures and behaviour the hidden tests expect: follow it literally.

Work like this:
1. Explore: list every function, class, method and file the requirements and interface name. \
When the task comes with a repository map, start from the files it lists instead of searching \
again; grep only for what it does not cover. A fix often spans several files (e.g. the main logic plus a helper or a \
test mock): change all of them. Use paths relative to the repository root. Keep outputs short.
2. Reproduce: when practical, write a small script in /tmp (never in the repository) that shows \
the problem, and run it.
3. Fix: make the smallest complete change in the source code with str_replace. Implement every \
name, parameter, return value and error the specification lists. Do not edit or add test files.
4. Verify: rerun your script, and run the existing tests closest to your change (for example \
`python -m pytest path/to/test_file.py -q -x`, `go test ./pkg/...`, or the project's own test \
command). Fix anything you broke.
5. Call finish with a one-line summary.

Rules:
- Use str_replace for edits; old_str must match the file exactly.
- Leave scratch files in /tmp, never in the repository.
- Do not try to install packages (pip, npm, apt): there is no network. Work with what is installed.
- Before calling finish, reread the requirements list and check each item against your change.
- Do not change tests, CI configuration, or dependency lock files unless the task requires it.
- When the interface changes a signature, follow the interface even if existing tests still call \
the old signature and stop compiling: the hidden tests replace those test files. Never keep an old \
signature just to make the existing tests build.
- Be efficient: you have a limited number of turns. Do not re-read files you have already seen.

Environment notes:
- Go: there is no network (GOPROXY=off). If the fix needs a module that is not in go.mod, see \
which versions are available with `ls $(go env GOMODCACHE)/cache/download/<module path>/@v/`, then \
`go get <module>@<version>` and `go mod tidy` work offline. Do not search the module cache with \
find or grep -r: it is huge. Run `go build ./<pkg>/ && go test ./<pkg>/ -run <TestName>` for \
the packages you touch.
- Python: run tests with `python -m pytest <file>::<test> -q -x`; a NameError or ImportError in \
your change fails every hidden test, so import the module you changed before you finish.
- JavaScript/TypeScript: run one test file with `npx jest <path>` (or the test script in \
package.json); do not run the whole suite or a full type check of the repository.
"""

TASK_PROMPT = """\
# Issue
{problem_statement}

# Requirements
{requirements}

# Interface
{interface}

Start by exploring the repository.
"""

RETRY_PROMPT = """\
Your patch was graded: {failed} of {total} hidden tests failed.{extra} Your edits are still in \
the repository. Re-read the issue, requirements and interface carefully: check every name, \
signature, default value, error type and message they specify, and look for cases you have not \
handled. Run the relevant existing tests. Fix the problem, then call finish again.
"""

NO_TOOL_NUDGE = (
    "Continue by calling a tool. When the fix is complete and verified, call finish.")

LAST_TURNS_WARNING = (
    "You have {left} turns left. Finish your edit now, and call finish as soon as it is in place.")

COMPACT_SYSTEM = """\
You compress the working log of a coding agent so it can continue with less context. Write a \
summary the agent can rely on instead of the log. Include, with exact file paths, function names \
and line numbers: what the agent found about the code involved, every edit it has made (file and \
what changed) and whether each is still in place, commands that worked for running tests or \
scripts (and ones that do not work in this environment), test results, and what is left to do. \
Quote verbatim the few short code fragments (signatures, the lines to change) the agent will need \
for its edit, so it does not have to read the files again. Do not invent anything. At most 700 \
words. Output only the summary."""

COMPACT_REQUEST = """\
{previous}Working log of the earlier turns:

{transcript}"""

COMPACTED_NOTE = """

# Your progress so far (summary of earlier turns; the most recent turns follow verbatim)
{summary}"""

PLAYBOOK_SYSTEM = """\
You maintain a playbook for a coding agent that fixes issues in many different repositories \
(Python, Go, JavaScript/TypeScript; SWE-bench style). Each task runs in an offline container with \
the repo and its test dependencies; hidden tests grade the fix. After a task you get a report: \
the issue, the outcome, and the agent's actions. Return the complete updated playbook.

Rules for the playbook:
- Every bullet must help on a task in a DIFFERENT repository and language than the report's. \
Never name the report's packages, files, functions, config fields or error messages.
- Lessons are about process: finding the relevant code quickly, running tests in these \
containers, matching the specification exactly, and mistakes that cost turns or broke grading \
(and what successful runs did instead).
- The playbook covers every task so far: keep earlier lessons that still hold, merge duplicates, \
drop weak ones; add a lesson only when the report supports it.
- At most 10 bullets, one or two sentences each, at most 300 words in total. Plain "- " bullets, \
no title. Output only the playbook."""

PLAYBOOK_REQUEST = """\
Current playbook:
{playbook}

{reports}"""

PLAYBOOK_NOTE = """

# Playbook: lessons from earlier tasks in this run
{playbook}"""

BUILD_GATE_PROMPT = """\
Before grading, an automatic check of the files you changed found problems:

{errors}

The hidden tests cannot pass while the code does not compile or uses undefined names. Fix these \
problems (keep the rest of your change), rerun the check yourself, and call finish again. If an \
error clearly existed before your change and is unrelated to it, ignore it and call finish."""

NO_EDIT_NUDGE = """\
You have used {used} turns and the repository still has no changes. Stop exploring now: you know \
enough to make a first version of the fix. Edit the source files with str_replace now, then verify \
and call finish. You have at most {left} turns left."""

REVIEW_SYSTEM = """\
You review a patch written by a coding agent before it is graded by hidden tests. The hidden tests \
check exactly what the issue, requirements and interface below specify: names, signatures, \
parameters and defaults, return values and types, error types and messages, and the edge cases \
listed. Compare the patch with that specification, item by item.

Report only concrete gaps you can point to: a specified item the patch does not implement, \
implements with a different name, signature, value or message, or implements only for some of the \
cases listed; and edits that obviously break existing behaviour. Do not suggest style changes, \
refactors, extra tests or anything the specification does not ask for. You see only the diff, not \
the whole repository: when something might be implemented outside the diff, do not report it.

If you find no such gap, answer exactly LGTM. Otherwise answer with a numbered list, at most 6 \
items, most important first; each item names the specification item, what the patch does instead, \
and the fix in one or two sentences."""

REVIEW_REQUEST = """\
{task}
{graded}
# The patch
```diff
{diff}
```"""

REVIEW_FEEDBACK = """\
A reviewer compared your patch with the issue, requirements and interface and reports these \
possible gaps:

{review}

The reviewer saw only the diff. For each point, check the code: fix the ones that are real, ignore \
the ones that are wrong, and keep the rest of your change. Verify, then call finish again."""

CANDIDATE_NOTE = """

Your checkout is {workdir} (a copy of {main}). Work only there: read, edit and run tests in \
{workdir}, never in {main}."""

TIME_WARNING = (
    "About {minutes} minute(s) of working time are left. Stop exploring: make sure your fix is in place "
    "in the repository, check it once, then call finish.")

CUT_OFF_NUDGE = (
    "Your reply was cut off because it was too long and contained no complete tool call. Reply with a "
    "single, shorter tool call (split a big edit into several str_replace calls).")

BUDGET_NOTE = "\n[budget: turn {turn}/{turns}, about {minutes} min left]"

ONESHOT_SYSTEM = """\
You are an expert software engineer. You fix an issue in a real repository in one pass: you get the \
issue, its specification (requirements and interface) and the relevant code, and you answer with \
the complete change as SEARCH/REPLACE blocks. Hidden tests check exactly what the specification \
says: every name, signature, default, return value, error type and message. Implement all of it.

Format each edit as:

path/to/file.ext
<<<<<<< SEARCH
exact lines copied from the file shown to you
=======
the new lines
>>>>>>> REPLACE

Rules:
- SEARCH must match the current file exactly, character for character, including indentation; \
include enough lines to be unique, and keep each block small (a few lines around the change).
- To create a new file, leave SEARCH empty; the REPLACE part is the whole file.
- Several blocks per file are fine; they are applied in order.
- Do not edit or add test files. Do not reformat unrelated code.
- If the specification changes a signature, update every caller shown to you as well.
Think briefly about what the hidden tests will check, then give all the blocks. Nothing else after \
the last block."""

ONESHOT_REQUEST = """\
{task}

{repo_map}

# Relevant code
{files}"""

ONESHOT_REPAIR = """\
Your edits were applied, with these problems:

{problems}

The repository now contains the blocks that did apply. Answer with SEARCH/REPLACE blocks against \
the CURRENT files that fix these problems (and finish anything left undone). Nothing else after \
the last block."""

ONESHOT_HANDOFF = """\
The harness applied your SEARCH/REPLACE blocks to this checkout ({workdir}) and checked the build. \
You can now use the tools to inspect, test and change the code."""
