# Claude Code Runner

A small local web UI for sending prompts to the `claude` CLI and watching the
run unfold live — the trajectory of tool calls and subagents as a tree and
as a graph, per-run cost/duration/turns, which files got changed and by how
much, and diffs you can open in a new tab.

## Prerequisites

- [Node.js](https://nodejs.org/) (no npm packages needed — the server has no dependencies)
- The [`claude` CLI](https://claude.com/product/claude-code), installed and already authenticated (`claude` should run on its own in a terminal)

## Starting the server

From this directory:

```bash
node server.js
```

By default it listens on **http://localhost:4756**. To use a different port:

```bash
PORT=8080 node server.js
```

Open the URL it prints in your browser, enter a working directory and a
prompt, and click **Run**. Only one prompt runs at a time; use **Send
follow-up** to continue the same conversation, or **New conversation** to
start fresh.

Stop the server with `Ctrl+C` in the terminal it's running in (or
`kill <pid>` if it's running in the background).

## Files

- `server.js` — the Node HTTP server. Spawns `claude -p ... --output-format stream-json`, parses its streaming output, and serves the UI and a live event feed over Server-Sent Events.
- `public/index.html` — the front end: the form, the live trajectory tree, the run graph, and the diff viewer.
- `diffs/` — hand-written `.patch` files documenting changes made to this app's own source; browsable from the "App changelog" panel at the top of the page.

## Notes

- **Bypass permission prompts** (a checkbox in the UI) runs `claude` with
  `--permission-mode bypassPermissions`, letting it edit files and run
  commands in the chosen directory without asking first. Only enable it for
  directories you trust — Claude Code auto-denies anything needing
  confirmation in non-interactive mode otherwise, so this is required for
  edits/commands to actually happen.
- The server has no authentication and isn't meant to be exposed beyond
  `localhost`.
