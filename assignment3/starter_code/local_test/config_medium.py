"""MEDIUM configuration: one candidate with a normal budget (70 turns, 20 min), near-miss
retries, build gate + interface check; no Expert review or playbook.

Your harness settings: add your own here, e.g. agent loop limits, tool limits, context management

The course API settings are environment variables, not config: CS2680_API_KEY and
CS2680_MODEL_EXPERT / CS2680_MODEL_STANDARD / CS2680_MODEL_STARTER (exported on the host,
passed in by run_all.py) and CS2680_BASE_URL (set by run_all.py to the egress proxy, the agent
container's only way to the API). Read them with os.environ; never hard-code the URL.
"""

# -- scheduling -------------------------------------------------------------------------------
NUM_WORKERS = 5                 # tasks worked on at once (the dispatcher allows at most 5 live)
RUN_LIMIT_S = 4 * 3600          # the dispatcher shuts the run down this long after the first task
RUN_SOFT_LIMIT_S = 3 * 3600 + 40 * 60   # after this, wrap up open tasks and open no new ones
NEXT_TASK_RETRY_S = 5           # wait between next_task() retries when 5 tasks are already live

# -- per task ---------------------------------------------------------------------------------
MODEL_TIER = "STANDARD"         # CS2680_MODEL_<tier> used for the first attempt
RETRY_MODEL_TIER = "STANDARD"   # model for attempts after a failed evaluation
MAX_TURNS = 70                  # model calls in the first attempt
RETRY_TURNS = 25                # model calls in each later attempt
MAX_ATTEMPTS = 3                # attempts of the best candidate; retries only for near misses (below)
NEAR_MISS_MAX_FAILED = 2        # a retry when at most this many hidden tests fail ...
NEAR_MISS_MAX_FRACTION = 0.10   # ... or at most this share of them (or the tests did not run)
TASK_WALL_S = 20 * 60           # agent-side work per candidate's first attempt (a retry gets half)
RETRY_MIN_RUN_LEFT_S = 50 * 60  # only retry when at least this much run time is left

# -- model calls ------------------------------------------------------------------------------
MAX_CONCURRENT_CALLS = 7        # model calls in flight across all tasks and candidates (the API allows 8)
RATE_LIMIT_WAIT_S = 3           # wait after a 429 (grows slowly), not counted against API_RETRIES
RATE_LIMIT_MAX_WAIT_S = 15 * 60 # total 429 waiting per call before giving up
API_RETRIES = 7                 # with the doubling backoff: about 8 minutes of 503s before giving up
API_BACKOFF_S = 4               # doubled after every failed attempt
EXTRA_MODEL_ARGS = {}            # e.g. {"temperature": 0.2}; empty = the API's defaults

# -- tools ------------------------------------------------------------------------------------
CMD_TIMEOUT_S = 180             # default bash timeout; the model may ask for up to CMD_TIMEOUT_MAX_S
CMD_TIMEOUT_MAX_S = 600
TOOL_OUTPUT_LIMIT = 10_000      # chars of one tool result the model sees (head + tail kept)
VIEW_MAX_LINES = 400            # lines one view_file call returns

# -- context management -----------------------------------------------------------------------
COMPACT_AT_CHARS = 160_000      # compact once the conversation is longer than this (~40k tokens)
COMPACT_KEEP_MESSAGES = 14      # the most recent messages kept verbatim; older ones are summarized
COMPACT_MODEL_TIER = "STANDARD" # model that writes the summary
COMPACT_INPUT_CHARS = 80_000    # transcript sent to the summarizer (head + tail kept)
CONTEXT_CHAR_LIMIT = 300_000    # safety net if compaction fails: older tool results are dropped ...
KEEP_RECENT_TOOL_RESULTS = 8    # ... except the most recent ones

# -- playbook: lessons carried from one task to the next ------------------------------------------
PLAYBOOK_ENABLED = False
PLAYBOOK_MODEL_TIER = "EXPERT"  # model that rewrites the playbook after each task
PLAYBOOK_MAX_CHARS = 2500       # longest playbook put into later tasks' system prompt
PLAYBOOK_DIGEST_CHARS = 10_000  # report on one task sent to the expert
PLAYBOOK_LOCK_WAIT_S = 3        # if another task is updating it, queue this report and move on
PLAYBOOK_MIN_RUN_LEFT_S = 15 * 60   # stop updating near the end of the run

# -- build/lint gate before grading ---------------------------------------------------------------
BUILD_GATE_ENABLED = True       # compile/lint the changed files before every evaluate()
BUILD_GATE_ROUNDS = 2           # times the model gets the errors back to fix them
BUILD_GATE_TURNS = 8            # model calls per fixing round
BUILD_GATE_WALL_S = 5 * 60      # agent-side time per fixing round
INTERFACE_CHECK = True          # the gate also checks every interface "Name:" exists in its "Path:"
BUILD_GATE_TIMEOUT_S = 300      # one check (e.g. go test -run '^$' on the changed packages)

# -- exploring without editing ----------------------------------------------------------------------
NO_EDIT_NUDGE_AT = (20, 40)     # model turns at which a repository with no changes gets a nudge

# -- cost report in run_logs/agent.log (estimate; $ per 1M tokens: input, cached input, output) --
PRICES = {"expert": (2.00, 0.10, 10.00), "standard": (0.08, 0.004, 0.28), "starter": (0.01, 0.0005, 0.04)}

# -- expert review of the patch against the specification, before grading -------------------------
REVIEW_ENABLED = False
REVIEW_MODEL_TIER = "EXPERT"    # one call per review: about 15k input tokens, ~$0.03-0.05
REVIEW_MAX_PER_TASK = 1         # reviews per candidate (its first attempt)
REVIEW_TURNS = 10               # model calls to act on a review
REVIEW_WALL_S = 6 * 60          # agent-side time to act on a review
REVIEW_DIFF_CHARS = 40_000      # longest diff sent to the reviewer (head + tail kept)

# -- parallel candidates ------------------------------------------------------------------------------
CANDIDATES = 1                  # independent first attempts per task, each in its own copy of the repo
CANDIDATE_ROOT = "/work"        # where the copies (git worktrees) go; the sandbox allows writes there
CANDIDATES_MIN_RUN_LEFT_S = 30 * 60   # near the end of the run, one candidate only

# -- deterministic work done by the harness instead of the model ------------------------------------
RECON_ENABLED = True            # grep the spec's names, find nearby tests, add the map to the first message
BUDGET_NOTE = False              # "[turn 12/60, ~14 min left]" on the newest tool result every turn
