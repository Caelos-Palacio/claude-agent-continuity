#!/usr/bin/env python3
# ---- shared motion-graphics kit (suite style: 800x450, 15 fps, #0d1117) ----
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 800, 450, 15
BG, FG, MUTED = (13, 17, 23), (230, 237, 243), (139, 148, 158)
ACCENT, OK, ERR, WARN = (88, 166, 255), (63, 185, 80), (248, 81, 73), (210, 153, 34)
PANEL, LINE = (22, 27, 34), (48, 54, 61)
HERE = Path(__file__).resolve().parent


def ease(x):
    """ease-in-out cubic on [0, 1]"""
    x = min(1.0, max(0.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def seg(t, a, b):
    """progress of t through [a, b], clamped to [0, 1]"""
    return min(1.0, max(0.0, (t - a) / (b - a))) if b > a else float(t >= a)


def window(t, a, b, fade=0.35):
    """alpha: eased fade in at a, eased fade out ending at b"""
    return ease(seg(t, a, a + fade)) * (1 - ease(seg(t, b - fade, b)))


_FONTS = {}
def font(size):
    if size not in _FONTS:
        for p in (Path.home() / ".local/share/fonts/JetBrainsMonoNerd/JetBrainsMonoNerdFont-Regular.ttf",
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")):
            if p.exists():
                _FONTS[size] = ImageFont.truetype(str(p), size)
                break
        else:
            _FONTS[size] = ImageFont.load_default()
    return _FONTS[size]


def mix(c, alpha, bg=BG):
    return tuple(round(bg[i] + (c[i] - bg[i]) * alpha) for i in range(3))


def text(d, xy, segs, size=13, alpha=1.0, bg=BG):
    """draw [(string, color), ...] left to right; returns end x"""
    x, y = xy
    f = font(size)
    for s, c in segs:
        d.text((x, y), s, font=f, fill=mix(c, alpha, bg))
        x += f.getlength(s)
    return x


def typed(segs, n):
    """first n characters of a segment list"""
    out = []
    for s, c in segs:
        if n <= 0:
            break
        out.append((s[:n], c))
        n -= len(s)
    return out


def wrap(s, cols):
    out, cur = [], ""
    for w in s.split(" "):
        if cur and len(cur) + 1 + len(w) > cols:
            out.append(cur); cur = w
        else:
            cur = (cur + " " + w).strip()
    return out + [cur]


def term_lines(d, t, lines, x, y, lh=17, size=13, alpha=1.0, cps=55):
    """lines: dicts {t, segs, type?}. Typed lines reveal at cps chars/s with a block cursor; others appear at t."""
    cur_line = None
    for i, L in enumerate(lines):
        if t < L["t"]:
            break
        segs = L["segs"]
        n = sum(len(s) for s, _ in segs)
        if L.get("type"):
            k = int((t - L["t"]) * cps)
            shown = typed(segs, min(n, k))
            if k < n + 4:
                cur_line = (i, sum(len(s) for s, _ in shown))
        else:
            shown = segs
        text(d, (x, y + i * lh), shown, size, alpha)
    if cur_line and int(t * 2.5) % 2 == 0:
        i, k = cur_line
        cx = x + font(size).getlength("M") * k
        d.rectangle([cx + 1, y + cur_line[0] * lh + 1, cx + 8, y + cur_line[0] * lh + size + 2], fill=mix(FG, 0.8 * alpha))


def rbox(d, xy, label, color, alpha=1.0, fill=PANEL, size=12, sub=None):
    x0, y0, x1, y1 = xy
    d.rounded_rectangle(xy, 6, fill=mix(fill, alpha), outline=mix(color, alpha), width=2)
    f = font(size)
    w = f.getlength(label)
    d.text(((x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - size / 2 - (6 if sub else 2)), label, font=f, fill=mix(FG, alpha))
    if sub:
        f2 = font(size - 2)
        d.text(((x0 + x1) / 2 - f2.getlength(sub) / 2, (y0 + y1) / 2 + 4), sub, font=f2, fill=mix(color, alpha))


def arrow(d, a, b, color, alpha=1.0, prog=1.0):
    (x0, y0), (x1, y1) = a, b
    xe, ye = x0 + (x1 - x0) * prog, y0 + (y1 - y0) * prog
    d.line([a, (xe, ye)], fill=mix(color, alpha), width=2)
    if prog >= 1:
        d.polygon([(x1, y1), (x1 - 7, y1 - 4), (x1 - 7, y1 + 4)], fill=mix(color, alpha))


def dot(d, a, b, p, color, alpha=1.0, r=4):
    x, y = a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p
    d.ellipse([x - r, y - r, x + r, y + r], fill=mix(color, alpha))


def frame_base(title, subtitle):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.text((24, 16), title, font=font(18), fill=ACCENT)
    d.text((24, 42), subtitle, font=font(12), fill=MUTED)
    return img, d


def panel(d, xy, title, alpha=1.0):
    d.rounded_rectangle(xy, 8, fill=mix(PANEL, 1), outline=LINE)
    for i, c in enumerate((ERR, WARN, OK)):
        d.ellipse([xy[0] + 12 + i * 16, xy[1] + 10, xy[0] + 21 + i * 16, xy[1] + 19], fill=mix(c, 0.7))
    d.text((xy[0] + 70, xy[1] + 7), title, font=font(11), fill=MUTED)


def build(render, duration, out, keep=None):
    """render(t) -> PIL image, deterministic. Assembles out (GIF) via ffmpeg palettegen."""
    n = int(round(duration * FPS))
    tmp = Path(tempfile.mkdtemp(prefix="demo-"))
    try:
        for i in range(n):
            render(i / FPS).save(tmp / f"f{i:04d}.png")
        pal = tmp / "pal.png"
        src = ["-framerate", str(FPS), "-i", str(tmp / "f%04d.png")]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-vf", "palettegen=stats_mode=full:max_colors=64", str(pal)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-i", str(pal), "-lavfi", "paletteuse=dither=none", "-loop", "0", str(out)], check=True)
        if keep:
            Path(keep).mkdir(parents=True, exist_ok=True)
            for i in keep_idx(n):
                shutil.copy(tmp / f"f{i:04d}.png", Path(keep) / f"f{i:04d}.png")
    finally:
        shutil.rmtree(tmp)
    print(f"{out}: {n} frames @ {FPS} fps = {n / FPS:.1f}s, {Path(out).stat().st_size / 1024:.0f} KiB")


def keep_idx(n):
    return [int(n * k) for k in (0.15, 0.35, 0.55, 0.75, 0.9)]
# ---- end kit ----

# ---------------------------------------------------------------------------
# claude-agent-continuity demo.
#   python3 docs/media/demo.py capture   -> runs the REAL CLI in a throwaway repo, writes capture.json
#   python3 docs/media/demo.py           -> renders docs/media/demo.gif from capture.json
# render(t) is a pure function of time; nothing random, no wall clock.
# ---------------------------------------------------------------------------
CAP = HERE / "capture.json"
BIN = HERE.parent.parent / "bin" / "claude-agent-continuity.mjs"
DURATION = 11.5


def sh(cwd, *a, env=None):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, env=env).stdout


def capture():
    root = Path(tempfile.mkdtemp(prefix="cac-demo-"))
    env = {**os.environ, "LC_ALL": "C", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
           "GIT_AUTHOR_NAME": "dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
           "GIT_COMMITTER_NAME": "dev", "GIT_COMMITTER_EMAIL": "dev@example.com"}
    g = lambda cwd, *a: sh(cwd, "git", *a, env=env)
    repo, wt = root / "myapp", root / "agent-auth"
    repo.mkdir(); g(repo, "init", "-q", "-b", "main")
    (repo / "app.js").write_text("// app\n"); g(repo, "add", "-A"); g(repo, "commit", "-qm", "init")
    g(repo, "worktree", "add", "-q", "-b", "agent/auth", str(wt))
    (wt / "docs/handoff").mkdir(parents=True); (wt / "docs/handoff/auth.md").write_text("# auth: login done, sessions next\n")
    g(wt, "add", "-A"); g(wt, "commit", "-qm", "auth: scaffold")
    (wt / "login.js").write_text("export const login = () => {}\n")       # the agent was cut off here:
    (wt / "session.js").write_text("export const session = () => {}\n")   # three files never committed
    with open(wt / "app.js", "a") as f: f.write("import './login.js'\n")
    run = lambda *a: sh(repo, "node", str(BIN), *a, env=env).replace(str(root), "~/work").rstrip("\n")
    steps = [("claude-agent-continuity status", run("status")),
             ("claude-agent-continuity wip", run("wip")),
             ("claude-agent-continuity resume agent-auth", run("resume", "agent-auth"))]
    CAP.write_text(json.dumps([{"cmd": c, "out": o} for c, o in steps], indent=2) + "\n")
    shutil.rmtree(root)


def colorize(line):
    if line.startswith("*"): return [("*", WARN), (line[1:], FG)] if "dirty" not in line else _row(line, WARN)
    if line.startswith("o "): return _row(line, OK)
    if line.startswith("WIP saved"): return [(line, OK)]
    if line.startswith("-- "): return [(line, ACCENT)]
    if line.startswith("    "): return [(line, MUTED)]
    return [(line, FG)]


def _row(line, c):
    out, i = [(line[0], c)], 1
    for key, col in (("3 dirty", WARN), ("clean", OK)):
        j = line.find(key)
        if j > 0:
            out += [(line[1:j], FG), (key, col), (line[j + len(key):], FG)]
            return out
    return out + [(line[1:], FG)]


def page(step, t0):
    lines = [{"t": t0, "type": True, "segs": [("$ ", OK), (step["cmd"], FG)]}]
    t = t0 + len(step["cmd"]) / 55 + 0.35
    for raw in step["out"].split("\n"):
        for w in (wrap(raw, 108) if len(raw) > 112 else [raw]):
            lines.append({"t": t, "segs": colorize(w) if w else [("", FG)]})
        t += 0.12
    return lines


def load():
    return json.loads(CAP.read_text())


# scene start times (s) and end times
SC = [(0.4, 3.4), (3.4, 5.6), (5.6, 11.0)]


def render(t, cap=None):
    cap = cap or load()
    img, d = frame_base("claude-agent-continuity", "never lose a cut-off Claude Code subagent's work")
    panel(d, (24, 68, 776, 280), "~/work/myapp  (output captured from the real CLI)")
    for (a, b), step in zip(SC, cap):
        al = window(t, a, b)
        if al > 0:
            lines = page(step, a + 0.2)
            layer = Image.new("RGB", (W, H), PANEL); ld = ImageDraw.Draw(layer)
            term_lines(ld, t, lines, 36, 92, lh=16, size=11, alpha=1.0, cps=55)
            mask = Image.new("L", (W, H), 0)
            ImageDraw.Draw(mask).rectangle([26, 90, 774, 278], fill=round(255 * al))
            img.paste(layer, (0, 0), mask)
    # diagram: cut-off agent -> WIP commit -> resume prompt
    dy = 322
    s1, s2, s3 = [ease(seg(t, a, a + 0.5)) for a in (0.9, 4.5, 6.9)]
    g = window(t, 0.3, 11.2)
    rbox(d, (40, dy, 240, dy + 72), "agent worktree", ERR if t < 4.5 else OK, g,
         sub="cut off, 3 files dirty" if t < 4.5 else "all committed")
    rbox(d, (300, dy, 500, dy + 72), "WIP commit", OK if t >= 4.5 else LINE, g * (0.35 + 0.65 * s2),
         sub="agent/auth +2" if t >= 5.0 else "pending")
    rbox(d, (560, dy, 760, dy + 72), "resume prompt", ACCENT if t >= 6.9 else LINE, g * (0.35 + 0.65 * s3),
         sub="reads handoff + git log" if t >= 7.4 else "pending")
    arrow(d, (240, dy + 32), (300, dy + 32), WARN, g * s2, ease(seg(t, 4.5, 5.0)))
    arrow(d, (500, dy + 32), (560, dy + 32), ACCENT, g * s3, ease(seg(t, 6.9, 7.4)))
    if 4.5 <= t < 5.0: dot(d, (240, dy + 32), (300, dy + 32), ease(seg(t, 4.5, 5.0)), WARN, g)
    if 6.9 <= t < 7.4: dot(d, (500, dy + 32), (560, dy + 32), ease(seg(t, 6.9, 7.4)), ACCENT, g)
    d.text((40, dy + 84), "zero dependencies · Node >= 18 · hook: SubagentStop + Stop", font=font(11), fill=mix(MUTED, g))
    return img


if __name__ == "__main__":
    if "capture" in sys.argv:
        capture()
    else:
        build(render, DURATION, HERE / "demo.gif", keep=(sys.argv[sys.argv.index("--keep") + 1] if "--keep" in sys.argv else None))
