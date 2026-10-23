#!/usr/bin/env python3
# ---- suite kit (identical in every repo): 800x450, 15 fps, pure render(t) -> image ----
import os, sys, shutil, subprocess, tempfile, json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H, FPS, S = 800, 450, 15, 2          # S = supersampling factor
BG, TEXT, MUTED = '#0d1117', '#e6edf3', '#8b949e'
ACCENT, OK, ERROR, WARN = '#58a6ff', '#3fb950', '#f85149', '#d29922'
FONT_DIRS = [Path.home() / '.local/share/fonts/JetBrainsMonoNerd']
FALLBACK = ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf']
FONT_FILES = {False: 'JetBrainsMonoNerdFont-Regular.ttf', True: 'JetBrainsMonoNerdFont-Bold.ttf'}


def rgb(h, a=1.0):
    h = h.lstrip('#')
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), round(255 * max(0, min(1, a))))


def clamp(x): return max(0.0, min(1.0, x))
def seg(t, a, b): return clamp((t - a) / (b - a))
def ease(x): x = clamp(x); return 4 * x ** 3 if x < .5 else 1 - (-2 * x + 2) ** 3 / 2   # cubic in-out
def lerp(a, b, k): return a + (b - a) * k
def typed(s, t, t0, cps=55): return s[:max(0, int((t - t0) * cps))]


_fonts = {}
def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        cands = [d / FONT_FILES[bold] for d in FONT_DIRS] + [Path(p) for p in FALLBACK]
        path = next(p for p in cands if p.exists())
        _fonts[key] = ImageFont.truetype(str(path), round(size * S))
    return _fonts[key]


class Cv:
    def __init__(self):
        self.img = Image.new('RGB', (W * S, H * S), BG)
        self.d = ImageDraw.Draw(self.img, 'RGBA')

    def text(self, x, y, s, size=13, color=TEXT, a=1.0, bold=False, anchor='la'):
        if a > .01 and s:
            self.d.text((x * S, y * S), s, font=font(size, bold), fill=rgb(color, a), anchor=anchor)

    def width(self, s, size=13, bold=False): return font(size, bold).getlength(s) / S

    def box(self, x0, y0, x1, y1, outline=MUTED, a=1.0, fill=None, fa=1.0, r=6, w=1):
        self.d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], radius=r * S,
                                 outline=rgb(outline, a), width=round(w * S),
                                 fill=rgb(fill, fa) if fill else None)

    def line(self, x0, y0, x1, y1, color=MUTED, a=1.0, w=1, dash=0):
        if not dash:
            self.d.line([x0 * S, y0 * S, x1 * S, y1 * S], fill=rgb(color, a), width=round(w * S))
            return
        n = int(max(abs(x1 - x0), abs(y1 - y0)) // dash)
        for i in range(0, n, 2):
            p, q = i / n, min(1, (i + 1) / n)
            self.d.line([lerp(x0, x1, p) * S, lerp(y0, y1, p) * S, lerp(x0, x1, q) * S, lerp(y0, y1, q) * S],
                        fill=rgb(color, a), width=round(w * S))

    def arrow(self, x0, y0, x1, y1, color=MUTED, a=1.0):
        self.line(x0, y0, x1, y1, color, a)
        s = 1 if x1 > x0 else -1
        self.d.polygon([(x1 * S, y1 * S), ((x1 - 7 * s) * S, (y1 - 4) * S), ((x1 - 7 * s) * S, (y1 + 4) * S)], fill=rgb(color, a))

    def dot(self, x, y, color=ACCENT, a=1.0, r=4.5):
        self.d.ellipse([(x - r) * S, (y - r) * S, (x + r) * S, (y + r) * S], fill=rgb(color, a))
        self.d.ellipse([(x - r * 2) * S, (y - r * 2) * S, (x + r * 2) * S, (y + r * 2) * S], fill=rgb(color, a * .16))


def envelope(t, T, fade=.5):
    """Loop envelope: blank at t=0 and t=T, so the GIF loops seamlessly."""
    return ease(seg(t, 0, fade)) * (1 - ease(seg(t, T - fade, T)))


def finish(cv, t, T):
    blank = Image.new('RGB', cv.img.size, BG)
    img = Image.blend(blank, cv.img, envelope(t, T))
    return img.resize((W, H), Image.LANCZOS)


def build(render, T, out, frame=None):
    if frame is not None:
        render(frame).save(out); return
    n = round(T * FPS)
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(n):
            render(i / FPS).save(f'{tmp}/f{i:04d}.png')
        pal = f'{tmp}/pal.png'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(FPS), '-i', f'{tmp}/f%04d.png',
                        '-vf', 'palettegen=max_colors=128:stats_mode=full', pal], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', str(FPS), '-i', f'{tmp}/f%04d.png', '-i', pal,
                        '-lavfi', 'paletteuse=dither=none', '-loop', '0', str(out)], check=True)
    print(f'{out}: {n} frames, {T}s, {Path(out).stat().st_size / 1e6:.2f} MB')


def cli(render, T, default_out):
    a = sys.argv[1:]
    if a[:1] == ['--frame']:          # python demo.py --frame 5.5 out.png
        build(render, T, a[2], float(a[1]))
    else:
        build(render, T, a[0] if a else default_out)
# ---- end kit ----

# ---- local helpers (per-demo): terminal panel, node boxes ----
HERE = Path(__file__).resolve().parent
PANEL, LINE = '#161b22', '#30363d'


def header(cv, title, subtitle):
    cv.text(32, 20, title, 22, ACCENT)
    cv.text(32, 52, subtitle, 13, MUTED)
    cv.line(32, 82, W - 32, 82, LINE)


def panel(cv, box, label):
    x0, y0, x1, y1 = box
    cv.box(x0, y0, x1, y1, LINE, 1, fill=PANEL, r=8)
    cv.text(x0 + 14, y0 + 8, label, 11, MUTED)


def node(cv, x0, y0, x1, y1, label, sub, color, idle=False):
    cv.box(x0, y0, x1, y1, LINE if idle else color, 1, fill=PANEL, r=6, w=2)
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    cv.text(cx, cy - 10, label, 13, MUTED if idle else TEXT, anchor='mm')
    if sub:
        cv.text(cx, cy + 12, sub, 12, MUTED if idle else color, anchor='mm')


def _plain(cv, x, y, s, size, color, a):
    """terminal text without programming ligatures (so '<!--' stays literal)"""
    if a <= .01 or not s:
        return
    try:
        cv.d.text((x * S, y * S), s, font=font(size), fill=rgb(color, a), features=['-liga', '-calt'])
    except (KeyError, ValueError, OSError):
        cv.text(x, y, s, size, color, a)


def term(cv, t, lines, x, y, lh, size, cps=55, al=1.0):
    """lines: [{t, segs:[(text,color)], type?}]; typed lines reveal at cps with a block cursor."""
    cur = None
    for i, L in enumerate(lines):
        if t < L['t']:
            break
        segs = L['segs']
        n = sum(len(s) for s, _ in segs)
        k = n if not L.get('type') else min(n, max(0, int((t - L['t']) * cps)))
        if L.get('type') and k < n + 1:
            cur = (i, k)
        cx = x
        left = k
        for s, c in segs:
            if left <= 0:
                break
            part = s[:left]
            _plain(cv, cx, y + i * lh, part, size, c, al)
            cx += cv.width(part, size)
            left -= len(s)
    if cur and int(t * 2.5) % 2 == 0:
        i, k = cur
        cx = x + cv.width('M', size) * k
        cv.d.rectangle([(cx + 1) * S, (y + i * lh + 1) * S, (cx + 8) * S, (y + i * lh + size + 3) * S], fill=rgb(TEXT, .8 * al))


def window(t, a, b, f=.35):
    return ease(seg(t, a, a + f)) * (1 - ease(seg(t, b - f, b)))


def wrap(s, cols):
    out, cur = [], ''
    for w in s.split(' '):
        if cur and len(cur) + 1 + len(w) > cols:
            out.append(cur); cur = w
        else:
            cur = (cur + ' ' + w).strip()
    return out + [cur]

# ---------------------------------------------------------------------------
# claude-agent-continuity demo.
#   python3 docs/media/demo.py capture   -> runs the REAL CLI in a throwaway repo, writes capture.json
#   python3 docs/media/demo.py [out.gif] -> renders docs/media/demo.gif from capture.json
#   python3 docs/media/demo.py --frame 5.5 out.png   (single frame)
# render(t) is a pure function of time; no randomness, no wall clock.
# ---------------------------------------------------------------------------
CAP = HERE / 'capture.json'
BIN = HERE.parent.parent / 'bin' / 'claude-agent-continuity.mjs'
T = 11.5
SC = [(0.5, 3.5), (3.5, 5.6), (5.6, 10.9)]


def sh(cwd, *a, env=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, env=env).stdout


def capture():
    root = Path(tempfile.mkdtemp(prefix='cac-demo-'))
    env = {**os.environ, 'LC_ALL': 'C', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null',
           'GIT_AUTHOR_NAME': 'dev', 'GIT_AUTHOR_EMAIL': 'dev@example.com',
           'GIT_COMMITTER_NAME': 'dev', 'GIT_COMMITTER_EMAIL': 'dev@example.com'}
    g = lambda cwd, *a: sh(cwd, 'git', *a, env=env)
    repo, wt = root / 'myapp', root / 'agent-auth'
    repo.mkdir(); g(repo, 'init', '-q', '-b', 'main')
    (repo / 'app.js').write_text('// app\n'); g(repo, 'add', '-A'); g(repo, 'commit', '-qm', 'init')
    g(repo, 'worktree', 'add', '-q', '-b', 'agent/auth', str(wt))
    (wt / 'docs/handoff').mkdir(parents=True); (wt / 'docs/handoff/auth.md').write_text('# auth: login done, sessions next\n')
    g(wt, 'add', '-A'); g(wt, 'commit', '-qm', 'auth: scaffold')
    (wt / 'login.js').write_text('export const login = () => {}\n')       # the agent was cut off here:
    (wt / 'session.js').write_text('export const session = () => {}\n')   # three files never committed
    with open(wt / 'app.js', 'a') as f: f.write("import './login.js'\n")
    run = lambda *a: sh(repo, 'node', str(BIN), *a, env=env).replace(str(root), '~/work').rstrip('\n')
    steps = [('claude-agent-continuity status', run('status')),
             ('claude-agent-continuity wip', run('wip')),
             ('claude-agent-continuity resume agent-auth', run('resume', 'agent-auth'))]
    CAP.write_text(json.dumps([{'cmd': c, 'out': o} for c, o in steps], indent=2) + '\n')
    shutil.rmtree(root)


def colorize(line):
    if line.startswith('WIP saved'): return [(line, OK)]
    if line.startswith('-- '): return [(line, ACCENT)]
    if line.startswith('    '): return [(line, MUTED)]
    for mark, col in (('* ', WARN), ('o ', OK)):
        if line.startswith(mark):
            key = '3 dirty' if mark == '* ' else 'clean'
            j = line.find(key)
            if j > 0:
                return [(mark, col), (line[2:j], TEXT), (key, col), (line[j + len(key):], TEXT)]
    return [(line, TEXT)]


def lines_for(step, t0):
    out = [{'t': t0, 'type': True, 'segs': [('$ ', OK), (step['cmd'], TEXT)]}]
    t = t0 + len(step['cmd']) / 55 + 0.35
    for raw in step['out'].split('\n'):
        for w in (wrap(raw, 84) if len(raw) > 112 else [raw]):
            out.append({'t': t, 'segs': colorize(w) if w else [('', TEXT)]})
        t += 0.12
    return out


def fit(step):
    n = max(len(l) if len(l) <= 112 else 84 for l in step['out'].split('\n'))
    return max(10, min(13, int(700 / (0.6 * n))))


def render(t, cap=None):
    cap = cap or json.loads(CAP.read_text())
    cv = Cv()
    header(cv, 'claude-agent-continuity', "never lose a cut-off Claude Code subagent's work")
    panel(cv, (32, 96, 768, 282), '~/work/myapp   output captured from the real CLI')
    for (a, b), step in zip(SC, cap):
        al = window(t, a, b)
        if al > 0:
            sz = fit(step)
            term(cv, t, lines_for(step, a + 0.2), 46, 122, sz + 6, sz, al=al)
    dy = 312
    cut = t < 4.5
    node(cv, 32, dy, 242, dy + 84, 'agent worktree', 'cut off, 3 files dirty' if cut else 'all committed', ERROR if cut else OK)
    node(cv, 295, dy, 505, dy + 84, 'WIP commit', 'agent/auth +2' if t >= 4.5 else 'pending', OK, idle=cut)
    node(cv, 558, dy, 768, dy + 84, 'resume prompt', 'reads handoff + git log' if t >= 6.9 else 'pending', ACCENT, idle=t < 6.9)
    for (x0, x1, c, a, b) in ((242, 295, OK, 4.5, 5.0), (505, 558, ACCENT, 6.9, 7.4)):
        p = ease(seg(t, a, b))
        if p > 0:
            cv.arrow(x0, dy + 42, x0 + (x1 - x0) * max(p, .05), dy + 42, c)
        if 0 < p < 1:
            cv.dot(x0 + (x1 - x0) * p, dy + 42, c)
    cv.text(32, 414, 'zero dependencies  |  Node >= 18  |  Claude Code hooks: SubagentStop, Stop', 12, MUTED)
    return finish(cv, t, T)


if __name__ == '__main__':
    if sys.argv[1:2] == ['capture']:
        capture()
    else:
        cli(render, T, HERE / 'demo.gif')
