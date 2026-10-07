"""Scaffold mytest/tasks/<instance_id>/ from an upstream fix commit.

usage: python3 mytest/scripts/new_task.py INSTANCE_ID REPO_URL FIX_COMMIT LOCAL_CLONE [--pip "extra deps"]

Writes, for the fix commit F with parent B (= base_commit):
  Dockerfile  python:3.12-slim with the repo at B in /app and all deps installed.
              The git history is replaced by a single commit, so the agent cannot
              find F (or anything after B) with `git log --all`.
  gold.diff   the non-test part of F (the real fix, never shown to the agent)
  test.diff   the test part of F (the held-out tests; applied by verify.sh via
              before_repo_set_cmd, never present in the image)
LOCAL_CLONE is any clone of the repo containing F (a blob-less clone is fine).
"""
import argparse
import os
import subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # mytest/

DOCKERFILE = """\
# mytest task {iid}: {repo} at {base} (parent of the upstream fix {fix})
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \\
    && rm -rf /var/lib/apt/lists/*
RUN git clone -q --filter=blob:none --no-checkout {repo} /app \\
    && git -C /app checkout -q {base} \\
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \\
    && git -C /app init -q \\
    && git -C /app add -A \\
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base {base}"
WORKDIR /app
RUN pip install --no-cache-dir -e . {pip}
"""

# For JavaScript/TypeScript repos (yarn v1 lockfile). The harness finds or bootstraps
# its own Python >= 3.10 in run_task.sh, so the image only needs node and git.
NODE_DOCKERFILE = """\
# mytest task {iid}: {repo} at {base} (parent of the upstream fix {fix})
FROM node:{node}-bullseye
RUN git clone -q --filter=blob:none --no-checkout {repo} /app \\
    && git -C /app checkout -q {base} \\
    # Keep only the base snapshot: no later commits (the fix) are reachable.
    && rm -rf /app/.git \\
    && git -C /app init -q \\
    && git -C /app add -A \\
    && git -C /app -c user.email=mytest@local -c user.name=mytest commit -qm "base {base}"
WORKDIR /app
RUN yarn install --frozen-lockfile --ignore-scripts --network-timeout 600000 && yarn cache clean
# The harness (madsLoop) needs Python >= 3.10; Debian bullseye ships 3.9 without pip (and
# its apt archive is end-of-life), so install a standalone CPython with uv instead.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh \\
    && UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python install 3.12 \\
    && ln -s "$(UV_PYTHON_INSTALL_DIR=/opt/uv-python uv python find 3.12)" /usr/local/bin/python3.12
"""


def git(clone, *args):
    return subprocess.run(["git", "-C", clone, *args], check=True, capture_output=True, text=True).stdout


def is_test(path):
    return (path.startswith(("tests/", "test/")) or "/tests/" in path or "/test/" in path
            or os.path.basename(path).startswith("test_"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iid")
    ap.add_argument("repo")
    ap.add_argument("fix")
    ap.add_argument("clone")
    ap.add_argument("--pip", default="pytest")
    ap.add_argument("--node", help="node major version: use the Node/yarn image instead of Python")
    a = ap.parse_args()

    fix = git(a.clone, "rev-parse", a.fix).strip()
    base = git(a.clone, "rev-parse", f"{fix}^").strip()
    files = git(a.clone, "diff", "--name-only", base, fix).split()
    tests = [f for f in files if is_test(f)]
    # The fix = everything except tests and documentation (data files the code reads are part of it).
    src = [f for f in files if not is_test(f) and not f.startswith(("doc/", "docs/", "releasenotes/", "stories/"))
           and "/website/" not in f and not f.endswith(".mdx")
           and not f.endswith((".md", ".rst")) and os.path.basename(f) not in ("CHANGELOG", "tox.ini")]

    out = os.path.join(HERE, "tasks", a.iid)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "Dockerfile"), "w") as f:
        if a.node:
            f.write(NODE_DOCKERFILE.format(iid=a.iid, repo=a.repo, base=base, fix=fix, node=a.node))
        else:
            f.write(DOCKERFILE.format(iid=a.iid, repo=a.repo, base=base, fix=fix, pip=a.pip))
    with open(os.path.join(out, "gold.diff"), "w") as f:
        f.write(git(a.clone, "diff", base, fix, "--", *src))
    with open(os.path.join(out, "test.diff"), "w") as f:
        f.write(git(a.clone, "diff", base, fix, "--", *tests))
    print(f"{out}\n  base_commit {base}\n  fix         {fix}\n  gold.diff   {src}\n  test.diff   {tests}")


if __name__ == "__main__":
    main()
