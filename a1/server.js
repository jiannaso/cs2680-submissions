#!/usr/bin/env node
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const PORT = process.env.PORT || 4756;
const PUBLIC_DIR = path.join(__dirname, 'public');
const DIFFS_DIR = path.join(__dirname, 'diffs'); // hand-written .patch files documenting this app's own dev history

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};

// --- single-job state -------------------------------------------------

const state = {
  running: false,
  proc: null,
  cwd: null,
  prompt: null,
  startedAt: null,
  exitCode: null,
  sessionId: null, // claude session id, kept between runs so follow-ups can --resume it
  status: 'idle', // idle | running | finished | failed
  failureReason: null,
  stopRequested: false,
  resultError: null, // error text from the stream's final `result` event
  stderrTail: '',
  resultStats: null, // { costUsd, numTurns } from the stream's final `result` event
  durationMs: null, // wall-clock time of the last run, measured here
  fileBaseline: new Map(), // file_path -> line array (or null if new) captured just before its first Edit/Write this run
  fileChanges: new Map(), // file_path -> { added, modified, deleted, diffText }, recomputed after every Edit/Write
  pendingWrites: new Map(), // tool_use id -> { name, input }, for Edit/Write calls awaiting their result
  trajectory: [], // structured trajectory items, replayed to clients that connect mid-run
  stdoutBuffer: '', // partial line carried over between stdout chunks
};

const clients = new Set(); // open SSE responses

// Per-file change summary for this run, plus the totals across all of them.
function fileChangesSummary() {
  const files = [];
  const totals = { added: 0, modified: 0, deleted: 0 };
  for (const [filePath, c] of state.fileChanges) {
    files.push({
      path: state.cwd ? path.relative(state.cwd, filePath) || filePath : filePath,
      absPath: filePath,
      added: c.added,
      modified: c.modified,
      deleted: c.deleted,
    });
    totals.added += c.added;
    totals.modified += c.modified;
    totals.deleted += c.deleted;
  }
  return { files, totals };
}

function publicState() {
  const { files, totals } = fileChangesSummary();
  return {
    running: state.running,
    cwd: state.cwd,
    prompt: state.prompt,
    startedAt: state.startedAt,
    exitCode: state.exitCode,
    status: state.status,
    hasSession: !!state.sessionId,
    stats: state.status === 'finished' || state.status === 'failed'
      ? {
          costUsd: state.resultStats ? state.resultStats.costUsd : null,
          numTurns: state.resultStats ? state.resultStats.numTurns : null,
          durationMs: state.durationMs,
          codeStats: totals,
          files,
        }
      : null,
    failureReason: state.failureReason,
  };
}

function sseWrite(res, event, data) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

function broadcast(event, data) {
  for (const res of clients) sseWrite(res, event, data);
}

// Tool names under which claude delegates work to a subagent.
const SUBAGENT_TOOLS = new Set(['Task', 'Agent']);

// Tools whose result we inspect to update code-change stats, keyed by the
// tool_use id so the matching tool_result can look the call back up.
const TRACKED_FILE_TOOLS = new Set(['Write', 'Edit']);

// The LCS (longest common subsequence) between two line arrays, as a flat
// list of ops in order, each carrying the 1-based line number it occupies in
// the old file (`aLine`) and/or the new one (`bLine`). This is the shared
// core both `diffLines` (counts only) and `unifiedDiff` (a real diff -u
// rendering) are built on, so the two never disagree with each other.
function computeOps(a, b) {
  const n = a.length, m = b.length;
  if (n * m > 400000) {
    // Too big to diff exactly without stalling the server; treat it as one
    // big replacement so the server stays responsive on huge files.
    let aLine = 0, bLine = 0;
    return [
      ...a.map((text) => ({ type: 'del', text, aLine: ++aLine, bLine: null })),
      ...b.map((text) => ({ type: 'ins', text, aLine: null, bLine: ++bLine })),
    ];
  }
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = 1; i <= n; i++) {
    for (let j = 1; j <= m; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  const rev = [];
  let i = n, j = m;
  while (i > 0 && j > 0) {
    if (a[i - 1] === b[j - 1]) { rev.push({ type: 'eq', text: a[i - 1] }); i--; j--; }
    else if (dp[i - 1][j] >= dp[i][j - 1]) { rev.push({ type: 'del', text: a[i - 1] }); i--; }
    else { rev.push({ type: 'ins', text: b[j - 1] }); j--; }
  }
  while (i > 0) { rev.push({ type: 'del', text: a[i - 1] }); i--; }
  while (j > 0) { rev.push({ type: 'ins', text: b[j - 1] }); j--; }
  rev.reverse();

  let aLine = 0, bLine = 0;
  for (const op of rev) {
    op.aLine = op.type === 'ins' ? null : ++aLine;
    op.bLine = op.type === 'del' ? null : ++bLine;
  }
  return rev;
}

// A run of consecutive deletions immediately followed by insertions (or vice
// versa) counts the overlap as "modified" lines and only the remainder as
// pure added/removed, matching how an in-place edit reads to a person.
function diffLines(a, b) {
  const ops = computeOps(a, b);
  let added = 0, removed = 0, modified = 0, k = 0;
  while (k < ops.length) {
    if (ops[k].type === 'eq') { k++; continue; }
    let del = 0, ins = 0;
    while (k < ops.length && ops[k].type !== 'eq') { ops[k].type === 'del' ? del++ : ins++; k++; }
    const mod = Math.min(del, ins);
    modified += mod;
    removed += del - mod;
    added += ins - mod;
  }
  return { added, removed, modified };
}

// A `diff -u`-style rendering of the change, with 3 lines of context around
// each hunk (adjacent hunks within 2×context of each other are merged, same
// as the real thing) — good enough to open in a browser tab and read.
function unifiedDiff(a, b, label) {
  const ops = computeOps(a, b);
  const CONTEXT = 3;
  const changeIdx = [];
  ops.forEach((op, idx) => { if (op.type !== 'eq') changeIdx.push(idx); });
  if (!changeIdx.length) return '';

  const ranges = [];
  let s = Math.max(0, changeIdx[0] - CONTEXT);
  let e = Math.min(ops.length, changeIdx[0] + 1 + CONTEXT);
  for (let k = 1; k < changeIdx.length; k++) {
    const ns = Math.max(0, changeIdx[k] - CONTEXT);
    if (ns <= e) e = Math.min(ops.length, changeIdx[k] + 1 + CONTEXT);
    else { ranges.push([s, e]); s = ns; e = Math.min(ops.length, changeIdx[k] + 1 + CONTEXT); }
  }
  ranges.push([s, e]);

  const lines = [`--- a/${label}`, `+++ b/${label}`];
  for (const [rs, re] of ranges) {
    const hunk = ops.slice(rs, re);
    const aNums = hunk.filter((o) => o.aLine != null).map((o) => o.aLine);
    const bNums = hunk.filter((o) => o.bLine != null).map((o) => o.bLine);
    const aStart = aNums.length ? aNums[0] : (rs > 0 ? ops[rs - 1].aLine + 1 : 1);
    const bStart = bNums.length ? bNums[0] : (rs > 0 ? ops[rs - 1].bLine + 1 : 1);
    lines.push(`@@ -${aStart},${aNums.length} +${bStart},${bNums.length} @@`);
    for (const op of hunk) {
      const prefix = op.type === 'eq' ? ' ' : op.type === 'del' ? '-' : '+';
      lines.push(prefix + op.text);
    }
  }
  return lines.join('\n');
}

// Snapshot a file's on-disk content the moment we see intent to Edit/Write
// it (before that tool actually runs), so we have a true "before" to diff
// against later — reading again once the result arrives would be too late.
function ensureBaseline(filePath) {
  if (state.fileBaseline.has(filePath)) return;
  let lines = null;
  try {
    lines = fs.readFileSync(filePath, 'utf8').split('\n');
  } catch {
    lines = null; // doesn't exist yet — this call creates it
  }
  state.fileBaseline.set(filePath, lines);
}

// Resolve a tracked Edit/Write call once its result is known: re-read the
// file from disk and diff it against the run's baseline for that path, so
// repeated edits to the same file always collapse into one net diff. Only
// successful calls count.
function resolveFileChange(toolUseId, isError) {
  const pending = state.pendingWrites.get(toolUseId);
  if (!pending) return;
  state.pendingWrites.delete(toolUseId);
  if (isError) return;
  const filePath = pending.input && pending.input.file_path;
  if (!filePath) return;

  let afterLines;
  try {
    afterLines = fs.readFileSync(filePath, 'utf8').split('\n');
  } catch {
    afterLines = [];
  }
  const beforeLines = state.fileBaseline.get(filePath) || [];
  const diff = diffLines(beforeLines, afterLines);
  const relLabel = state.cwd ? path.relative(state.cwd, filePath) || filePath : filePath;
  state.fileChanges.set(filePath, {
    added: diff.added,
    modified: diff.modified,
    deleted: diff.removed,
    diffText: unifiedDiff(beforeLines, afterLines, relLabel),
  });
}

function emitTrajectory(item) {
  item.ts = new Date().toISOString();
  state.trajectory.push(item);
  broadcast('trajectory', item);
}

// Turn one parsed `claude --output-format stream-json` line into trajectory
// items: the assistant's text as it's produced, and every tool call with its
// name and input, each broadcast the moment it appears in the stream.
function processStreamEvent(evt) {
  // Events produced inside a subagent carry the id of the tool call that spawned it.
  const parentId = evt.parent_tool_use_id || null;
  if (typeof evt.session_id === 'string' && evt.session_id) state.sessionId = evt.session_id;
  if (evt.type === 'assistant' && evt.message && Array.isArray(evt.message.content)) {
    for (const block of evt.message.content) {
      if (block.type === 'text' && block.text) {
        emitTrajectory({ kind: 'text', parentId, text: block.text });
      } else if (block.type === 'tool_use') {
        const item = { kind: 'tool_call', parentId, id: block.id, name: block.name, input: block.input };
        if (SUBAGENT_TOOLS.has(block.name)) {
          const input = block.input || {};
          item.subagent = {
            description: input.description || input.subagent_type || 'Subagent task',
            type: input.subagent_type || null,
            prompt: typeof input.prompt === 'string' ? input.prompt : '',
          };
        }
        if (TRACKED_FILE_TOOLS.has(block.name) && block.input && typeof block.input.file_path === 'string') {
          ensureBaseline(block.input.file_path);
          state.pendingWrites.set(block.id, { name: block.name, input: block.input });
        }
        emitTrajectory(item);
      }
    }
  } else if (evt.type === 'user' && evt.message && Array.isArray(evt.message.content)) {
    for (const block of evt.message.content) {
      if (block.type !== 'tool_result') continue;
      const text = Array.isArray(block.content)
        ? block.content.map((c) => (c && c.type === 'text' ? c.text : '')).join('\n')
        : String(block.content == null ? '' : block.content);
      emitTrajectory({ kind: 'tool_result', parentId, toolUseId: block.tool_use_id, text, isError: !!block.is_error });
      resolveFileChange(block.tool_use_id, !!block.is_error);
    }
  } else if (evt.type === 'result') {
    state.resultStats = {
      costUsd: typeof evt.total_cost_usd === 'number' ? evt.total_cost_usd : null,
      numTurns: typeof evt.num_turns === 'number' ? evt.num_turns : null,
    };
    if (evt.is_error) {
      const detail = typeof evt.result === 'string' && evt.result ? evt.result : '';
      state.resultError = [evt.subtype, detail].filter(Boolean).join(': ') || 'Run reported an error';
    }
    if (typeof evt.result === 'string' && evt.result) {
      emitTrajectory({ kind: 'final_result', text: evt.result, isError: !!evt.is_error });
    }
  }
}

function consumeStdoutChunk(chunk) {
  state.stdoutBuffer += chunk;
  let idx;
  while ((idx = state.stdoutBuffer.indexOf('\n')) !== -1) {
    const line = state.stdoutBuffer.slice(0, idx).trim();
    state.stdoutBuffer = state.stdoutBuffer.slice(idx + 1);
    if (!line) continue;
    parseAndProcessLine(line);
  }
}

function parseAndProcessLine(line) {
  let evt;
  try {
    evt = JSON.parse(line);
  } catch {
    emitTrajectory({ kind: 'raw', text: line });
    return;
  }
  processStreamEvent(evt);
}

// Settle the run exactly once, deciding finished vs failed and why.
function finishRun({ code, signal, spawnError }) {
  if (!state.running) return;
  const rest = state.stdoutBuffer.trim();
  if (rest) parseAndProcessLine(rest);
  state.stdoutBuffer = '';
  state.running = false;
  state.proc = null;
  state.exitCode = code;

  let reason = null;
  if (spawnError) reason = `Could not launch claude: ${spawnError.message}`;
  else if (state.stopRequested) reason = 'Stopped by user';
  else if (state.resultError) reason = state.resultError;
  else if (code !== 0) {
    const tail = state.stderrTail.trim().split('\n').slice(-3).join('\n');
    reason = (signal ? `claude was killed by ${signal}` : `claude exited with code ${code}`) + (tail ? `\n${tail}` : '');
  }
  state.durationMs = Date.now() - Date.parse(state.startedAt);
  state.status = reason ? 'failed' : 'finished';
  state.failureReason = reason;
  broadcast('done', { exitCode: code, status: state.status, failureReason: reason, stats: publicState().stats });
  broadcast('status', publicState());
}

function startRun({ prompt, cwd, bypassPermissions, resume }, res) {
  if (state.running) {
    return sendJSON(res, 409, { error: 'A prompt is already running. Wait for it to finish or stop it first.' });
  }
  if (typeof prompt !== 'string' || !prompt.trim()) {
    return sendJSON(res, 400, { error: 'Prompt is required.' });
  }
  if (resume && !state.sessionId) {
    return sendJSON(res, 400, { error: 'There is no previous conversation to continue.' });
  }
  // Sessions are stored per directory, so a follow-up must reuse the original one.
  if (!resume && (typeof cwd !== 'string' || !cwd.trim())) {
    return sendJSON(res, 400, { error: 'Working directory is required.' });
  }

  let resolvedCwd = resume ? state.cwd : path.resolve(cwd.trim());
  let stat;
  try {
    stat = fs.statSync(resolvedCwd);
    // Resolve symlinks (e.g. macOS /tmp -> /private/tmp) so file paths the
    // tools report — which are already real paths — relativize cleanly
    // against it instead of via a spurious "../.." detour.
    if (!resume) resolvedCwd = fs.realpathSync(resolvedCwd);
  } catch {
    return sendJSON(res, 400, { error: `Directory does not exist: ${resolvedCwd}` });
  }
  if (!stat.isDirectory()) {
    return sendJSON(res, 400, { error: `Not a directory: ${resolvedCwd}` });
  }

  const args = ['-p', prompt, '--output-format', 'stream-json', '--verbose'];
  if (resume) args.push('--resume', state.sessionId);
  if (bypassPermissions) args.push('--permission-mode', 'bypassPermissions');

  let proc;
  try {
    proc = spawn('claude', args, { cwd: resolvedCwd, env: process.env, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (err) {
    return sendJSON(res, 500, { error: `Failed to launch claude: ${err.message}` });
  }

  state.running = true;
  state.proc = proc;
  state.cwd = resolvedCwd;
  state.prompt = prompt;
  state.startedAt = new Date().toISOString();
  state.exitCode = null;
  state.status = 'running';
  state.failureReason = null;
  state.stopRequested = false;
  state.resultError = null;
  state.resultStats = null;
  state.durationMs = null;
  state.stderrTail = '';
  state.fileBaseline.clear();
  state.fileChanges.clear();
  state.pendingWrites.clear();
  if (!resume) {
    state.trajectory = [];
    state.sessionId = null;
  }
  state.stdoutBuffer = '';
  emitTrajectory({ kind: 'user_prompt', text: prompt });

  broadcast('status', publicState());

  proc.stdout.on('data', (d) => consumeStdoutChunk(d.toString()));
  proc.stderr.on('data', (d) => {
    state.stderrTail = (state.stderrTail + d.toString()).slice(-2000);
    emitTrajectory({ kind: 'stderr', text: d.toString() });
  });
  proc.on('close', (code, signal) => finishRun({ code, signal }));
  proc.on('error', (err) => {
    emitTrajectory({ kind: 'stderr', text: `[error running claude: ${err.message}]\n` });
    finishRun({ code: -1, spawnError: err });
  });

  return sendJSON(res, 202, { ok: true, ...publicState() });
}

// --- tiny http plumbing -------------------------------------------------

function sendJSON(res, status, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body) });
  res.end(body);
}

function escapeHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// A small standalone page for `GET /api/diff`, styled to match the app, with
// added/removed lines colored the same way the rest of the UI colors them.
function renderDiffPage(label, diffText) {
  const body = diffText
    .split('\n')
    .map((l) => {
      let cls = 'ctx';
      if (l.startsWith('+++') || l.startsWith('---')) cls = 'hdr';
      else if (l.startsWith('@@')) cls = 'hunk';
      else if (l.startsWith('+')) cls = 'add';
      else if (l.startsWith('-')) cls = 'del';
      return `<span class="${cls}">${escapeHtml(l) || ' '}</span>`;
    })
    .join('\n');
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Diff — ${escapeHtml(path.basename(label))}</title>
<style>
  body { background: #0f1115; color: #e6e8eb; margin: 0; padding: 20px 24px; }
  h1 {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-size: 0.9rem; font-weight: 600; color: #9aa2af; margin: 0 0 14px;
  }
  pre {
    white-space: pre-wrap; word-break: break-word; margin: 0;
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
    font-size: 0.82rem; line-height: 1.5;
  }
  .hdr { color: #9aa2af; }
  .hunk { color: #7fb0ff; }
  .add { color: #4caf7d; background: rgba(76, 175, 125, 0.1); display: block; }
  .del { color: #e05a4e; background: rgba(224, 90, 78, 0.1); display: block; }
  .ctx { color: #c9d1d9; display: block; }
</style></head><body>
<h1>${escapeHtml(label)}</h1>
<pre>${body}</pre>
</body></html>`;
}

function serveStatic(req, res, urlPath) {
  const rel = urlPath === '/' ? '/index.html' : urlPath;
  const filePath = path.normalize(path.join(PUBLIC_DIR, rel));
  if (!filePath.startsWith(PUBLIC_DIR)) {
    res.writeHead(403);
    return res.end('Forbidden');
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404);
      return res.end('Not found');
    }
    const ext = path.extname(filePath);
    res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  if (req.method === 'GET' && url.pathname === '/api/status') {
    return sendJSON(res, 200, publicState());
  }

  if (req.method === 'GET' && url.pathname === '/api/stream') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    });
    res.write(': connected\n\n');
    sseWrite(res, 'status', publicState());
    for (const item of state.trajectory) sseWrite(res, 'trajectory', item);
    clients.add(res);
    req.on('close', () => clients.delete(res));
    return;
  }

  // Only serves diffs this run actually computed — the query string can't be
  // used to read arbitrary files off disk.
  if (req.method === 'GET' && url.pathname === '/api/diff') {
    const filePath = url.searchParams.get('file');
    const change = filePath && state.fileChanges.get(filePath);
    if (!change) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('No diff available for that file (it may belong to a different run).');
    }
    const label = state.cwd ? path.relative(state.cwd, filePath) || filePath : filePath;
    const html = renderDiffPage(label, change.diffText || '(no textual change)');
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(html);
  }

  // Lists the .patch/.diff files checked into diffs/ — a record of changes
  // made to this app's own source, not to anything a run touched.
  if (req.method === 'GET' && url.pathname === '/api/dev-diffs') {
    let files = [];
    try {
      files = fs.readdirSync(DIFFS_DIR)
        .filter((f) => f.endsWith('.patch') || f.endsWith('.diff'))
        .map((f) => {
          const st = fs.statSync(path.join(DIFFS_DIR, f));
          return { name: f, size: st.size, mtime: st.mtime.toISOString() };
        })
        .sort((a, b) => a.mtime < b.mtime ? 1 : -1);
    } catch {
      files = [];
    }
    return sendJSON(res, 200, { files });
  }

  // Serves one of those files, rendered the same way as a run's diffs. The
  // name must be a bare filename (no path segments) that actually exists in
  // diffs/, so this can't be used to read arbitrary files off disk.
  if (req.method === 'GET' && url.pathname === '/api/dev-diff') {
    const name = url.searchParams.get('name') || '';
    const isSafeName = name && name === path.basename(name) && (name.endsWith('.patch') || name.endsWith('.diff'));
    let text = null;
    if (isSafeName) {
      try { text = fs.readFileSync(path.join(DIFFS_DIR, name), 'utf8'); } catch { text = null; }
    }
    if (text == null) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('No such dev diff.');
    }
    const html = renderDiffPage(name, text);
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    return res.end(html);
  }

  if (req.method === 'POST' && url.pathname === '/api/run') {
    let body = '';
    req.on('data', (c) => {
      body += c;
      if (body.length > 1e6) req.destroy(); // guard against runaway bodies
    });
    req.on('end', () => {
      let data;
      try {
        data = JSON.parse(body || '{}');
      } catch {
        return sendJSON(res, 400, { error: 'Invalid JSON body.' });
      }
      startRun(data, res);
    });
    return;
  }

  if (req.method === 'POST' && url.pathname === '/api/reset') {
    if (state.running) return sendJSON(res, 409, { error: 'Cannot start a new conversation while a prompt is running.' });
    state.sessionId = null;
    state.trajectory = [];
    state.status = 'idle';
    state.failureReason = null;
    state.exitCode = null;
    state.fileBaseline.clear();
    state.fileChanges.clear();
    state.pendingWrites.clear();
    broadcast('reset', {});
    broadcast('status', publicState());
    return sendJSON(res, 200, { ok: true });
  }

  if (req.method === 'POST' && url.pathname === '/api/stop') {
    if (state.running && state.proc) {
      state.stopRequested = true;
      state.proc.kill('SIGTERM');
      return sendJSON(res, 200, { ok: true });
    }
    return sendJSON(res, 200, { ok: false, message: 'Nothing is running.' });
  }

  if (req.method === 'GET') {
    return serveStatic(req, res, url.pathname);
  }

  res.writeHead(404);
  res.end('Not found');
});

server.listen(PORT, () => {
  console.log(`Claude Code runner UI: http://localhost:${PORT}`);
});
