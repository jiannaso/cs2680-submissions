# Repository workflow

At the end of every user turn, before you stop, commit the work from that turn.

- If this turn created or edited files, stage only those files and make one git commit.
- Before that commit, package the Claude Code, Cursor, and Codex session logs for the whole repository and include the archive in the same commit. From the repository root, run `python3 package_claude_sessions.py --path . --output claude-sessions.tar.gz`, then stage `claude-sessions.tar.gz`.
- Write a short commit message that says what the turn changed.
- If this turn left the tree clean and the session archive is unchanged, do not make an empty commit.
- Do not stage unrelated dirty files, secrets, or gitignored files.
- Do not amend, rebase, or force-push unless the user asks.
