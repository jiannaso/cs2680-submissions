#!/bin/bash
# docker_env.sh — container setup, runs before madsLoop.py.
#
# The harness needs Python 3.10+, the standard library, and openai. openai goes
# into its own directory, /tmp/madsloop_deps (madsLoop.py puts it on sys.path),
# NOT into the image's Python: the repo's tests run with that interpreter, and
# openai pulls in pydantic, httpx, typing_extensions, ... which could change
# what they import. run_task.sh exports AGENT_PY, the interpreter that runs
# madsLoop.py, so the wheels match its Python version.
set -e
export PIP_ROOT_USER_ACTION=ignore PIP_DISABLE_PIP_VERSION_CHECK=1
PY="${AGENT_PY:-python3}"
OPENAI_SPEC="openai==3.24.0"  # the version madsLoop was developed and tested with

if ! "$PY" -m pip --version >/dev/null 2>&1; then
  # Some images ship a Python without pip (e.g. Debian's /usr/bin/python3.11).
  # Bootstrap pip into a private directory instead of the system site-packages.
  "$PY" -c 'import urllib.request as u; u.urlretrieve("https://bootstrap.pypa.io/get-pip.py", "/tmp/get-pip.py")'
  "$PY" /tmp/get-pip.py -q --target /tmp/madsloop_pip
  export PYTHONPATH=/tmp/madsloop_pip  # only for the pip call below, not for the agent
fi
"$PY" -m pip install -q --target /tmp/madsloop_deps "$OPENAI_SPEC"
