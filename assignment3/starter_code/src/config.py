"""Your harness settings: add your own here, e.g. agent loop limits, tool limits, context management

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
MAX_TURNS = 60                  # model calls in the first attempt
RETRY_TURNS = 25                # model calls in each later attempt
MAX_ATTEMPTS = 2                # evaluations per task (each grading can take up to 35 min)
TASK_WALL_S = 35 * 60           # agent-side work per task, not counting grading
RETRY_MIN_RUN_LEFT_S = 50 * 60  # only retry when at least this much run time is left

# -- model calls ------------------------------------------------------------------------------
API_RETRIES = 5
API_BACKOFF_S = 4               # doubled after every failed attempt
EXTRA_MODEL_ARGS = {}            # e.g. {"temperature": 0.2}; empty = the API's defaults

# -- tools ------------------------------------------------------------------------------------
CMD_TIMEOUT_S = 180             # default bash timeout; the model may ask for up to CMD_TIMEOUT_MAX_S
CMD_TIMEOUT_MAX_S = 600
TOOL_OUTPUT_LIMIT = 10_000      # chars of one tool result the model sees (head + tail kept)
VIEW_MAX_LINES = 400            # lines one view_file call returns

# -- context management -----------------------------------------------------------------------
COMPACT_AT_CHARS = 100_000      # compact once the conversation is longer than this (~25k tokens)
COMPACT_KEEP_MESSAGES = 10      # the most recent messages kept verbatim; older ones are summarized
COMPACT_MODEL_TIER = "STANDARD" # model that writes the summary
COMPACT_INPUT_CHARS = 80_000    # transcript sent to the summarizer (head + tail kept)
CONTEXT_CHAR_LIMIT = 240_000    # safety net if compaction fails: older tool results are dropped ...
KEEP_RECENT_TOOL_RESULTS = 8    # ... except the most recent ones

# -- playbook: lessons carried from one task to the next ------------------------------------------
PLAYBOOK_ENABLED = True
PLAYBOOK_MODEL_TIER = "EXPERT"  # model that rewrites the playbook after each task
PLAYBOOK_MAX_CHARS = 2500       # longest playbook put into later tasks' system prompt
PLAYBOOK_DIGEST_CHARS = 10_000  # report on one task sent to the expert
PLAYBOOK_LOCK_WAIT_S = 60       # if another task is updating it, queue this report and move on
PLAYBOOK_MIN_RUN_LEFT_S = 15 * 60   # stop updating near the end of the run
