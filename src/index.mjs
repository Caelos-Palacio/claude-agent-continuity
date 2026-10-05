// claude-agent-continuity: zero-dependency CLI. Git is only ever invoked via execFileSync with argv (no shell).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const DEFAULT_MESSAGE = 'WIP {branch}: automatic save when an agent stopped (claude-agent-continuity)';
export const DEFAULT_HANDOFF = ['docs/handoff/**/*.md', 'docs/HANDOFF.md', 'HANDOFF.md'];
const BIN = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../bin/claude-agent-continuity.mjs');
const FALLBACK_IDENTITY = ['-c', 'user.name=claude-agent-continuity', '-c', 'user.email=claude-agent-continuity@users.noreply.invalid'];
const BUSY_MARKERS = ['MERGE_HEAD', 'REBASE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_LOG', 'index.lock'];

export const git = (cwd, ...args) => {
  try { return execFileSync('git', ['-C', cwd, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'], maxBuffer: 1 << 28 }); }
  catch { return null; }
};
const gitT = (cwd, ...a) => { const r = git(cwd, ...a); return r === null ? null : r.trim(); };

export function parseArgs(argv) {
  const o = { cmd: null, pos: [], handoff: [], json: false, quiet: false, scope: null, includeMain: false };
  const takes = new Set(['--repo', '--base', '--handoff', '--message', '--command', '--settings']);
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (takes.has(a)) { const v = argv[++i]; if (a === '--handoff') o.handoff.push(v); else o[a.slice(2)] = v; }
    else if (a === '--json') o.json = true;
    else if (a === '--quiet' || a === '--hook') o.quiet = true;
    else if (a === '--global') o.scope = 'global';
    else if (a === '--project') o.scope = 'project';
    else if (a === '--include-main') o.includeMain = true;
    else if (a === '-h' || a === '--help') o.cmd = 'help';
    else if (!o.cmd) o.cmd = a;
    else o.pos.push(a);
  }
  return o;
}

export function globToRegex(g) {
  let r = '';
  for (let i = 0; i < g.length; i++) {
    const c = g[i];
    if (c === '*' && g[i + 1] === '*') { if (g[i + 2] === '/') { r += '(?:.*/)?'; i += 2; } else { r += '.*'; i++; } }
    else if (c === '*') r += '[^/]*';
    else if (c === '?') r += '[^/]';
    else r += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${r}$`);
}

export function resolveBase(cwd, explicit) {
  if (explicit) return explicit;
  const head = gitT(cwd, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD');
  if (head) return head;
  for (const b of ['main', 'master']) if (gitT(cwd, 'rev-parse', '--verify', '-q', `refs/heads/${b}`)) return b;
  const first = listRaw(cwd)[0];
  return first?.branch ?? 'HEAD';
}

function listRaw(cwd) {
  const out = git(cwd, 'worktree', 'list', '--porcelain');
  if (out === null) return [];
  return out.split(/\n\n+/).map((blk) => {
    const w = {};
    for (const line of blk.split('\n')) {
      if (line.startsWith('worktree ')) w.dir = line.slice(9);
      else if (line.startsWith('branch ')) w.branch = line.slice(7).replace(/^refs\/heads\//, '');
      else if (line === 'bare') w.bare = true;
      else if (line === 'detached') w.detached = true;
      else if (line.startsWith('prunable')) w.prunable = true;
    }
    return w;
  }).filter((w) => w.dir);
}

const isIgnoredPath = (p) => p.split('/').includes('node_modules');

export function collectWorktrees(opts = {}) {
  const cwd = path.resolve(opts.repo ?? process.cwd());
  if (gitT(cwd, 'rev-parse', '--git-dir') === null) throw new Error(`not a git repository: ${cwd}`);
  const base = resolveBase(cwd, opts.base);
  const patterns = (opts.handoff?.length ? opts.handoff : DEFAULT_HANDOFF).map(globToRegex);
  const all = listRaw(cwd);
  const list = all.filter((w, i) => !w.bare && !w.prunable && (i > 0 || opts.includeMain) && fs.existsSync(w.dir));
  const baseOk = gitT(cwd, 'rev-parse', '--verify', '-q', `${base}^{commit}`) !== null;
  return { base, worktrees: list.map((w) => inspect(w, base, baseOk, patterns, cwd)) };
}

function inspect(w, base, baseOk, patterns, repoCwd) {
  const dir = w.dir, name = path.basename(dir);
  const gd = gitT(dir, 'rev-parse', '--absolute-git-dir');
  const marker = gd ? BUSY_MARKERS.find((f) => fs.existsSync(path.join(gd, f))) : 'unreadable git dir';
  const status = (git(dir, 'status', '--porcelain', '--untracked-files=all') ?? '').split('\n').filter(Boolean);
  const dirtyLines = status.filter((l) => !isIgnoredPath(l.slice(3).split(' -> ').pop().replace(/^"|"$/g, '')));
  const conflicts = status.some((l) => /^(UU|AA|DD|AU|UA|DU|UD) /.test(l));
  const head = gitT(dir, 'rev-parse', 'HEAD');
  const ahead = baseOk ? +(gitT(dir, 'rev-list', '--count', `${base}..HEAD`) ?? 0) : 0;
  const merged = baseOk && head ? git(repoCwd, 'merge-base', '--is-ancestor', head, base) !== null : false;
  const changed = new Set();
  if (baseOk) for (const f of (git(dir, 'diff', '--name-only', `${base}...HEAD`) ?? '').split('\n')) if (f) changed.add(f);
  for (const l of status) changed.add(l.slice(3).split(' -> ').pop());
  const handoffs = [...changed].filter((f) => patterns.some((re) => re.test(f)) && fs.existsSync(path.join(dir, f))).sort();
  const reason = marker ? `git operation in progress (${marker})` : conflicts ? 'unresolved conflicts' : w.detached ? 'detached HEAD' : null;
  return { name, dir, branch: w.branch ?? null, detached: !!w.detached, ahead, merged, dirty: dirtyLines.length, busy: !!reason, skipReason: reason,
    handoffs, last: gitT(dir, 'log', '-1', '--format=%cr | %s') ?? '' };
}

function newlySymlinked(dir) {
  const raw = git(dir, 'diff', '--cached', '--raw', '-z', '--no-renames') ?? '';
  const t = raw.split('\0'), bad = [];
  for (let i = 0; i + 1 < t.length; i += 2) {
    const m = /^:(\d+) (\d+) \S+ \S+ (\S)/.exec(t[i]);
    if (!m) continue;
    const p = t[i + 1];
    if (isIgnoredPath(p) || (m[2] === '120000' && m[3] === 'A')) bad.push(p);
  }
  return bad;
}

export function wip(opts = {}) {
  const { base, worktrees } = collectWorktrees(opts);
  const template = opts.message ?? DEFAULT_MESSAGE;
  const saved = [], skipped = [];
  for (const w of worktrees) {
    if (!w.dirty) continue;
    if (w.busy) { skipped.push({ name: w.name, reason: w.skipReason }); continue; }
    if (git(w.dir, 'add', '-A') === null) { skipped.push({ name: w.name, reason: 'git add failed' }); continue; }
    const bad = newlySymlinked(w.dir);
    if (bad.length) git(w.dir, 'reset', '-q', '--', ...bad);
    const files = (git(w.dir, 'diff', '--cached', '--name-only') ?? '').split('\n').filter(Boolean).length;
    if (!files) { skipped.push({ name: w.name, reason: 'only ignored paths (node_modules/symlinks) changed' }); continue; }
    const msg = template.replaceAll('{branch}', w.branch ?? 'HEAD').replaceAll('{name}', w.name).replaceAll('{count}', String(files));
    const hasId = gitT(w.dir, 'config', 'user.email');
    const id = hasId ? [] : FALLBACK_IDENTITY;
    if (git(w.dir, ...id, '-c', 'commit.gpgsign=false', 'commit', '-q', '--no-verify', '-m', msg) !== null) saved.push({ name: w.name, branch: w.branch, files });
    else { git(w.dir, 'reset', '-q'); skipped.push({ name: w.name, reason: 'commit failed' }); }
  }
  return { base, saved, skipped };
}

export function resumeMessages(opts = {}, name) {
  const { base, worktrees } = collectWorktrees(opts);
  return worktrees.filter((w) => (!name || w.name === name) && (!w.merged || w.dirty)).map((w) => {
    const files = w.handoffs.map((f) => path.join(w.dir, f));
    const read = files.length ? files.join(' and ') : `git log ${base}..HEAD`;
    const where = w.branch ? `branch ${w.branch}` : 'a detached HEAD';
    return { name: w.name, branch: w.branch, dir: w.dir, handoffs: files, message:
      `You were interrupted (usage limit, watchdog or kill). Your work is saved as WIP commits on ${where} in ${w.dir}. ` +
      `Read ${read} first, check git status and git log, then continue the task where you left off. ` +
      `Commit often and keep the handoff up to date.` };
  });
}

export function hookBlock(command) {
  const entry = (extra) => ({ hooks: [{ type: 'command', command, timeout: 60, ...extra }] });
  return { SubagentStop: [entry({ statusMessage: 'Saving WIP of agent worktrees' })], Stop: [entry({})] };
}
export const defaultHookCommand = () => `f=${JSON.stringify(BIN)}; [ -f "$f" ] && node "$f" wip --quiet || true`;

export function installHook(opts = {}) {
  const command = opts.command ?? defaultHookCommand();
  const block = hookBlock(command);
  const target = opts.settings ? path.resolve(opts.settings)
    : opts.scope === 'global' ? path.join(process.env.CLAUDE_CONFIG_DIR ?? path.join(os.homedir(), '.claude'), 'settings.json')
    : opts.scope === 'project' ? path.join(path.resolve(opts.repo ?? process.cwd()), '.claude', 'settings.json') : null;
  if (!target) return { written: false, block: { hooks: block } };
  let cur = {}, existed = fs.existsSync(target), backup = null;
  if (existed) {
    try { cur = JSON.parse(fs.readFileSync(target, 'utf8') || '{}'); } catch (e) { throw new Error(`${target} is not valid JSON; refusing to modify it (${e.message})`); }
    if (cur === null || typeof cur !== 'object' || Array.isArray(cur)) throw new Error(`${target} is not a JSON object; refusing to modify it`);
  }
  cur.hooks ??= {};
  const added = [];
  for (const ev of Object.keys(block)) {
    const groups = Array.isArray(cur.hooks[ev]) ? cur.hooks[ev] : (cur.hooks[ev] = []);
    const has = groups.some((g) => (g?.hooks ?? []).some((h) => typeof h?.command === 'string' && (h.command === command || h.command.includes('claude-agent-continuity'))));
    if (!has) { groups.push(block[ev][0]); added.push(ev); }
  }
  if (added.length) {
    if (existed) { backup = `${target}.bak-${new Date().toISOString().replace(/[:.]/g, '-')}`; fs.copyFileSync(target, backup); }
    else fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, JSON.stringify(cur, null, 2) + '\n');
  }
  return { written: added.length > 0, target, added, backup };
}

const USAGE = `claude-agent-continuity <command> [options]
  status                 list agent worktrees: branch, commits ahead of base, dirty files, handoff docs
  wip [--quiet]          commit pending changes in each worktree as a WIP commit (safe: skips merge/rebase/conflicts/index.lock)
  resume [name]          print a ready-to-send resume prompt per unfinished worktree
  install-hook [--global|--project]   print (default) or merge the SubagentStop/Stop hook into Claude Code settings
options: --repo <dir>  --base <branch>  --handoff <glob> (repeatable)  --message <template: {branch} {name} {count}>
         --json  --quiet|--hook (hook mode: never exits non-zero)  --include-main  --settings <file>  --command <hook command>`;

export function main(argv, io = { out: (s) => console.log(s), err: (s) => console.error(s) }) {
  const o = parseArgs(argv);
  const cmd = o.cmd ?? 'status';
  const emit = (data, text) => io.out(o.json ? JSON.stringify(data, null, 2) : text);
  try {
    if (cmd === 'help') { io.out(USAGE); return 0; }
    if (cmd === 'wip') {
      const r = wip(o);
      if (o.quiet) { if (r.saved.length) io.out(JSON.stringify({ systemMessage: `WIP saved in: ${r.saved.map((s) => `${s.name} (${s.files} files)`).join(', ')}` })); }
      else emit(r, r.saved.length ? `WIP saved: ${r.saved.map((s) => `${s.name} (${s.files} files)`).join(', ')}` : 'nothing to save');
      if (!o.quiet && !o.json) for (const s of r.skipped) io.out(`skipped ${s.name}: ${s.reason}`);
    } else if (cmd === 'status') {
      const { base, worktrees } = collectWorktrees(o);
      const active = worktrees.filter((w) => !w.merged || w.dirty);
      if (o.json) io.out(JSON.stringify({ base, worktrees }, null, 2));
      else {
        const lines = [`${worktrees.length} worktrees (base ${base}), ${active.length} with unintegrated work`, ''];
        for (const w of active) lines.push(`${w.busy ? '!' : w.dirty ? '*' : 'o'} ${w.name.padEnd(28)} ${(w.branch ?? '(detached)').padEnd(30)} +${w.ahead} commits  ${w.dirty ? `${w.dirty} dirty` : 'clean'}${w.busy ? ` [${w.skipReason}]` : ''}  handoff: ${w.handoffs.join(',') || '-'}`, `    ${w.last}`);
        const stale = worktrees.filter((w) => w.merged && !w.dirty).length;
        if (stale) lines.push('', `${stale} merged and clean (removable with \`git worktree remove\`)`);
        io.out(lines.join('\n'));
      }
    } else if (cmd === 'resume') {
      const r = resumeMessages(o, o.pos[0]);
      emit(r, r.length ? r.map((m) => `-- ${m.name} (${m.branch ?? 'detached'})\n${m.message}\n`).join('\n') : 'nothing to resume');
    } else if (cmd === 'install-hook') {
      const r = installHook(o);
      if (o.json) io.out(JSON.stringify(r, null, 2));
      else if (!r.target) io.out(`Add this to "hooks" in your Claude Code settings.json (or run with --global / --project):\n${JSON.stringify({ hooks: r.block.hooks }, null, 2)}`);
      else io.out(r.written ? `Hook added (${r.added.join(', ')}) in ${r.target}${r.backup ? `\nbackup: ${r.backup}` : ''}` : `Hook already present in ${r.target}; nothing changed`);
    } else { io.err(`unknown command: ${cmd}\n${USAGE}`); return o.quiet ? 0 : 2; }
    return 0;
  } catch (e) {
    if (!o.quiet) { io.err(`error: ${e.message}`); return 1; }
    return 0;
  }
}
