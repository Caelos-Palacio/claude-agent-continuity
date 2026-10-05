import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const BIN = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../bin/claude-agent-continuity.mjs');
const sh = (cwd, ...a) => execFileSync('git', ['-C', cwd, ...a], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim();
const run = (cwd, ...a) => spawnSync('node', [BIN, ...a], { cwd, encoding: 'utf8', env: { ...process.env, GIT_CONFIG_GLOBAL: '/dev/null', GIT_CONFIG_SYSTEM: '/dev/null' } });

function fixture() {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'cac-')));
  const repo = path.join(root, 'repo');
  fs.mkdirSync(repo);
  sh(repo, 'init', '-q', '-b', 'main');
  for (const [k, v] of [['user.name', 'T'], ['user.email', 't@example.invalid'], ['commit.gpgsign', 'false']]) sh(repo, 'config', k, v);
  fs.writeFileSync(path.join(repo, 'a.txt'), 'one\n');
  sh(repo, 'add', '-A'); sh(repo, 'commit', '-q', '-m', 'init');
  const wt = (name) => { const d = path.join(root, name); sh(repo, 'worktree', 'add', '-q', '-b', `agent/${name}`, d); return d; };
  return { root, repo, wt };
}

test('dirty worktree is committed as WIP', () => {
  const { repo, wt } = fixture(); const d = wt('w1');
  fs.writeFileSync(path.join(d, 'new.txt'), 'x'); fs.appendFileSync(path.join(d, 'a.txt'), 'two\n');
  const r = run(repo, 'wip', '--json'); assert.equal(r.status, 0);
  assert.equal(JSON.parse(r.stdout).saved[0].files, 2);
  assert.equal(sh(d, 'status', '--porcelain'), '');
  assert.match(sh(d, 'log', '-1', '--format=%s'), /^WIP agent\/w1:/);
  assert.ok(!/Co-Authored/.test(sh(d, 'log', '-1', '--format=%B')));
});

test('custom message template and --base', () => {
  const { repo, wt } = fixture(); const d = wt('w2');
  fs.writeFileSync(path.join(d, 'n'), '1');
  run(repo, 'wip', '--base', 'main', '--message', 'save {name} on {branch} ({count})');
  assert.equal(sh(d, 'log', '-1', '--format=%s'), 'save w2 on agent/w2 (1)');
});

test('worktree in merge with conflicts is untouched', () => {
  const { repo, wt } = fixture(); const d = wt('w3');
  fs.writeFileSync(path.join(d, 'a.txt'), 'branch\n'); sh(d, 'commit', '-qam', 'b');
  fs.writeFileSync(path.join(repo, 'a.txt'), 'main\n'); sh(repo, 'commit', '-qam', 'm');
  assert.throws(() => sh(d, 'merge', 'main'));
  const before = sh(d, 'rev-parse', 'HEAD'), st = sh(d, 'status', '--porcelain');
  const r = run(repo, 'wip', '--json');
  assert.equal(JSON.parse(r.stdout).saved.length, 0);
  assert.equal(sh(d, 'rev-parse', 'HEAD'), before); assert.equal(sh(d, 'status', '--porcelain'), st);
  assert.ok(fs.readFileSync(path.join(d, 'a.txt'), 'utf8').includes('<<<<<<<'));
});

test('index.lock worktree is untouched', () => {
  const { repo, wt } = fixture(); const d = wt('w4');
  fs.writeFileSync(path.join(d, 'n'), '1');
  fs.writeFileSync(path.join(sh(d, 'rev-parse', '--absolute-git-dir'), 'index.lock'), '');
  const before = sh(repo, 'rev-parse', 'agent/w4');
  const r = run(repo, 'wip', '--json'); assert.equal(r.status, 0);
  assert.equal(JSON.parse(r.stdout).skipped[0].name, 'w4');
  assert.equal(sh(repo, 'rev-parse', 'agent/w4'), before);
});

test('node_modules and new symlinks are not committed', () => {
  const { repo, wt, root } = fixture(); const d = wt('w5');
  fs.mkdirSync(path.join(d, 'node_modules/pkg'), { recursive: true });
  fs.writeFileSync(path.join(d, 'node_modules/pkg/i.js'), 'x');
  fs.symlinkSync(root, path.join(d, 'link'));
  fs.writeFileSync(path.join(d, 'real.txt'), 'r');
  run(repo, 'wip');
  const files = sh(d, 'show', '--name-only', '--format=', 'HEAD').split('\n');
  assert.deepEqual(files, ['real.txt']);
});

test('only node_modules changes -> no commit', () => {
  const { repo, wt } = fixture(); const d = wt('w6');
  fs.mkdirSync(path.join(d, 'node_modules'), { recursive: true }); fs.writeFileSync(path.join(d, 'node_modules/x'), '1');
  const before = sh(d, 'rev-parse', 'HEAD');
  run(repo, 'wip'); assert.equal(sh(d, 'rev-parse', 'HEAD'), before);
});

test('resume names the handoff; status lists worktree', () => {
  const { repo, wt } = fixture(); const d = wt('w7');
  fs.mkdirSync(path.join(d, 'docs/handoff'), { recursive: true });
  fs.writeFileSync(path.join(d, 'docs/handoff/w7.md'), '# h');
  run(repo, 'wip');
  const r = JSON.parse(run(repo, 'resume', '--json').stdout);
  assert.equal(r.length, 1);
  assert.ok(r[0].message.includes(path.join(d, 'docs/handoff/w7.md')));
  assert.ok(run(repo, 'status').stdout.includes('w7'));
  assert.ok(run(repo, 'resume', 'nope').stdout.includes('nothing to resume'));
  const custom = JSON.parse(run(repo, 'resume', '--json', '--handoff', 'none/**').stdout);
  assert.deepEqual(custom[0].handoffs, []);
});

test('works from inside a worktree and non-.claude paths', () => {
  const { wt } = fixture(); const d = wt('w8');
  fs.writeFileSync(path.join(d, 'n'), '1');
  assert.equal(JSON.parse(run(d, 'wip', '--json').stdout).saved.length, 1);
});

test('hook mode never exits non-zero', () => {
  const nogit = fs.mkdtempSync(path.join(os.tmpdir(), 'cac-ng-'));
  for (const args of [['wip', '--quiet'], ['wip', '--quiet', '--repo', '/nonexistent/x'], ['bogus', '--quiet'], ['status', '--hook']]) {
    const r = run(nogit, ...args); assert.equal(r.status, 0, args.join(' '));
  }
  assert.notEqual(run(nogit, 'wip').status, 0);
});

test('hook quiet output is a systemMessage JSON', () => {
  const { repo, wt } = fixture(); const d = wt('w9'); fs.writeFileSync(path.join(d, 'n'), '1');
  assert.ok(JSON.parse(run(repo, 'wip', '--quiet').stdout).systemMessage.includes('w9'));
  assert.equal(run(repo, 'wip', '--quiet').stdout, '');
});

test('install-hook merges, preserves, backs up, is idempotent', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'cac-s-')); const f = path.join(dir, 'settings.json');
  fs.writeFileSync(f, JSON.stringify({ model: 'x', hooks: { Stop: [{ hooks: [{ type: 'command', command: 'echo hi' }] }] } }));
  const r = JSON.parse(run(dir, 'install-hook', '--settings', f, '--json').stdout);
  assert.deepEqual(r.added.sort(), ['Stop', 'SubagentStop']); assert.ok(fs.existsSync(r.backup));
  const s = JSON.parse(fs.readFileSync(f, 'utf8'));
  assert.equal(s.model, 'x'); assert.equal(s.hooks.Stop.length, 2); assert.equal(s.hooks.Stop[0].hooks[0].command, 'echo hi');
  assert.equal(JSON.parse(run(dir, 'install-hook', '--settings', f, '--json').stdout).written, false);
  fs.writeFileSync(f, '{bad'); assert.notEqual(run(dir, 'install-hook', '--settings', f).status, 0);
  assert.equal(fs.readFileSync(f, 'utf8'), '{bad');
  assert.ok(JSON.parse(run(dir, 'install-hook').stdout.split('\n').slice(1).join('\n')).hooks.SubagentStop);
});
