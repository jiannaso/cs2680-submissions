"""Keep the fix as unstaged changes on top of the starting commit (handout 0.1).

The patch is collected afterwards with `git diff` in <workdir>. If the agent
commits, stages, or moves HEAD (e.g. from inside a script, which the bash
filter cannot see), that diff comes out empty or against the wrong base.
"""
import subprocess
import sys


def _git(workdir, *args):
    return subprocess.run(["git", "-C", workdir, *args], capture_output=True, text=True)


def start_commit(workdir):
    """HEAD at the start of the run, or None if workdir is not a git repo."""
    r = _git(workdir, "rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def restore_unstaged(workdir, start):
    """Point HEAD back at `start`, keeping the working tree, and unstage everything."""
    if not start:
        return
    head = start_commit(workdir)
    if head != start:
        print(f"[madsLoop] HEAD moved during the run ({start[:8]} -> {(head or '?')[:8]}); "
              "resetting it, keeping the working tree", file=sys.stderr)
        _git(workdir, "reset", "-q", "--soft", start)
    # Mixed reset: drops anything staged by `git add`, files on disk are untouched.
    _git(workdir, "reset", "-q")
