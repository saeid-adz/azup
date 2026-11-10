#!/usr/bin/env python3
"""Generates the animated SVGs used in the README.

    python assets/make_assets.py            # writes assets/banner.svg and assets/demo.svg
    python assets/make_assets.py --at 9.5   # static snapshot of the demo at t=9.5s (for previews)
"""
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).parent
SNAP = float(sys.argv[sys.argv.index("--at") + 1]) if "--at" in sys.argv else None

C = dict(bg="#0d1117", bar="#161b22", fg="#e6edf3", dim="#7d8590", az="#3aa9ff", cy="#56d4dd",
         gr="#3fb950", ye="#d29922", rd="#f85149", pu="#b38bff")
FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'DejaVu Sans Mono', monospace"
CW, LH, FS, X0, Y0 = 8.1, 19.5, 13.5, 26, 84
T = 19.0   # loop length (s)


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def vis(t_in, t_out):
    """opacity animation (or static value in snapshot mode)."""
    if SNAP is not None:
        return f' opacity="{1 if t_in <= SNAP < t_out else 0}"'
    k = [0, t_in / T, min(1, (t_in + .04) / T), min(1, t_out / T), min(1, (t_out + .04) / T), 1]
    for i in range(1, len(k)):
        k[i] = max(k[i], k[i - 1])
    return ('<animate attributeName="opacity" values="0;0;1;1;0;0" '
            f'keyTimes="{";".join(f"{x:.4f}" for x in k)}" dur="{T}s" repeatCount="indefinite"/>')


_clip = [0]
defs, body = [], []


def line(parts, row, t_in, t_out, typed=None, x=X0):
    """One terminal line. parts = [(text, colour)], typed = (start, seconds) for a typing effect."""
    parts = [(t, C.get(c, c)) for t, c in parts]
    n = sum(len(t) for t, _ in parts)
    y = Y0 + row * LH
    spans = "".join(f'<tspan fill="{c}">{esc(t)}</tspan>' for t, c in parts)
    clip = ""
    inner = ""
    if typed:
        _clip[0] += 1
        cid = f"c{_clip[0]}"
        t0, dur = typed
        if SNAP is not None:
            w = n * CW * min(1, max(0, (SNAP - t0) / dur))
            inner = ""
            defs.append(f'<clipPath id="{cid}"><rect x="{x}" y="{y - FS}" width="{w:.1f}" height="{LH + 4}"/></clipPath>')
        else:
            ts = [0] + [t0 + dur * i / n for i in range(n + 1)]
            ws = [0] + [i * CW for i in range(n + 1)]
            ts = [t / T for t in ts]
            for i in range(1, len(ts)):
                ts[i] = max(ts[i], ts[i - 1])
            defs.append(f'<clipPath id="{cid}"><rect x="{x}" y="{y - FS}" width="0" height="{LH + 4}">'
                        f'<animate attributeName="width" calcMode="discrete" values="{";".join(f"{w:.1f}" for w in ws)}" '
                        f'keyTimes="{";".join(f"{t:.4f}" for t in ts)}" dur="{T}s" repeatCount="indefinite"/></rect></clipPath>')
        clip = f' clip-path="url(#{cid})"'
    anim = vis(t_in, t_out)
    if SNAP is not None:
        body.append(f'<text x="{x}" y="{y}" textLength="{n * CW:.1f}" lengthAdjust="spacingAndGlyphs" xml:space="preserve"{clip}{anim}>{spans}</text>')
    else:
        body.append(f'<text x="{x}" y="{y}" textLength="{n * CW:.1f}" lengthAdjust="spacingAndGlyphs" xml:space="preserve"{clip} opacity="0">{spans}{anim}</text>')


def azzy(row, t_in, t_out, msg, eyes="●  ●", mouth="‿"):
    """Mascot + speech bubble made of text lines."""
    m = ["    ╷    ", " ╭──┴──╮ ", f" │ {eyes[0]} {eyes[-1]} │ ", f" │  {mouth}  │ ", " ╰─────╯ "]
    w = len(msg)
    bub = ["╭" + "─" * (w + 2) + "╮", f"│ {msg} │", "╰" + "─" * (w + 2) + "╯"]
    for r in range(5):
        parts = [(m[r], "az")]
        if 1 <= r <= 3:
            parts += [("◂ " if r == 2 else "  ", "az"), (bub[r - 1], "dim")]
        line(parts, row + r, t_in, t_out)


def bar(i, n, w=18):
    f = int(w * i / n)
    return [("▕", "dim"), ("█" * f, "az"), ("░" * (w - f), "dim"), ("▏", "dim")]


# ───────────────────────────── demo scenes ─────────────────────────────
S1, S2, S3, S4 = (0.3, 3.6), (3.6, 9.2), (9.2, 15.4), (15.4, 18.7)

# scene 1: launch + mascot loading
line([("$ ", "gr"), ("azup", "fg")], 0, S1[0], S1[1], typed=(0.5, 0.6))
azzy(2, 1.5, 2.6, "⠹ Inspecting your toolbox…", "●  ●", "o")
azzy(2, 2.6, S1[1], "⠼ Reticulating Azure splines…", "●  ●", "o")

# scene 2: status + Clippy-style suggestion + menu
t = S2[0]
rows2 = [
    ([("CLI", "az")], 0),
    ([("Azure CLI            ", "fg"), ("2.86.0   ", "dim"), ("✔ up to date", "gr")], 1),
    ([("Azure Developer CLI  ", "fg"), ("1.25.4   ", "dim"), ("▲ update available ", "ye"), ("→ 1.37.3", "gr")], 2),
    ([("Bicep                ", "fg"), ("0.45.15  ", "dim"), ("● installed", "cy")], 3),
    ([("POWERSHELL", "az")], 4),
    ([("Az PowerShell        ", "fg"), ("16.2.0   ", "dim"), ("▲ update available ", "ye"), ("→ 16.5.0", "gr")], 5),
    ([("Microsoft Graph SDK  ", "fg"), ("–        ", "dim"), ("○ not installed", "dim")], 6),
]
for parts, r in rows2:
    line([("  ", "fg")] + parts, r, t + 0.15 * r, S2[1])
azzy(8, 5.4, S2[1], "It looks like 2 tools are outdated. Update them?", "●  ●", "‿")
line([("  What now?", "fg")], 14, 6.6, S2[1])
line([("   ", "fg"), ("❯ ", "az"), ("Update everything", "fg"), ("      2 outdated", "dim")], 15, 6.8, S2[1])
line([("     Select components…", "dim")], 16, 6.8, S2[1])
line([("     Doctor", "dim")], 17, 6.8, S2[1])

# scene 3: update run
line([("  ▕", "dim"), ("░" * 18, "dim"), ("▏ ", "dim"), ("1/2  ", "dim"), ("▶ ", "az"), ("Updating Azure Developer CLI", "fg")], 0, 9.4, S3[1])
line([("    $ brew upgrade azd", "dim")], 1, 9.7, S3[1], typed=(9.7, 0.7))
line([("==> Upgrading azd  1.25.4 -> 1.37.3", "fg")], 2, 10.6, S3[1], x=X0 + 4 * CW)
line([("🍺  /opt/homebrew/Cellar/azd/1.37.3: 11 files, 52MB", "dim")], 3, 11.4, S3[1], x=X0 + 4 * CW)
line([("  " , "fg")] + bar(1, 2) + [(" 2/2  ", "dim"), ("▶ ", "az"), ("Updating Az PowerShell", "fg")], 5, 12.0, S3[1])
line([("    $ pwsh -Command Update-Module Az", "dim")], 6, 12.3, S3[1], typed=(12.3, 0.9))
line([("  Summary", "fg")], 8, 13.6, S3[1])
line([("   ✔ ", "gr"), ("Azure Developer CLI   ", "fg"), ("update   ", "dim"), ("  8s", "dim")], 9, 13.9, S3[1])
line([("   ✔ ", "gr"), ("Az PowerShell        ", "fg"), ("update   ", "dim"), (" 71s", "dim")], 10, 14.2, S3[1])
line([("  ", "fg")] + bar(2, 2) + [(" 2/2", "dim")], 12, 14.5, S3[1])

# scene 4: celebration
azzy(2, 15.7, S4[1], "All done! Your toolbox is looking sharp.", "●  ●", "‿")
line([("$ ", "gr"), ("azup joke", "fg")], 8, 16.8, S4[1], typed=(16.8, 0.6))
azzy(9, 17.5, S4[1], "It works on my subscription.", "◔  ◔", "─")

# confetti
random.seed(7)
conf = []
for i in range(46):
    x = random.randint(30, 740)
    col = random.choice(["#f25022", "#7fba00", "#00a4ef", "#ffb900", "#b38bff"])
    r = random.choice([2.5, 3, 3.5])
    t0 = 15.5 + random.random() * 0.7
    fall = 2.2 + random.random() * 1.0
    if SNAP is not None:
        p = (SNAP - t0) / fall
        if 0 <= p <= 1:
            conf.append(f'<circle cx="{x}" cy="{80 + p * 420:.0f}" r="{r}" fill="{col}"/>')
        continue
    k = [0, t0 / T, (t0 + fall) / T, min(1, (t0 + fall + .05) / T), 1]
    conf.append(
        f'<circle cx="{x}" cy="60" r="{r}" fill="{col}" opacity="0">'
        f'<animateTransform attributeName="transform" type="translate" values="0 0;0 0;0 440;0 440;0 0" keyTimes="{";".join(f"{a:.4f}" for a in k)}" dur="{T}s" repeatCount="indefinite"/>'
        f'<animate attributeName="opacity" values="0;0;1;0;0" keyTimes="{";".join(f"{a:.4f}" for a in k)}" dur="{T}s" repeatCount="indefinite"/></circle>')

W, H = 800, 520
chrome = f'''<rect width="{W}" height="{H}" rx="12" fill="{C['bg']}" stroke="#30363d"/>
<path d="M0 12a12 12 0 0 1 12-12h{W - 24}a12 12 0 0 1 12 12v30H0z" fill="{C['bar']}"/>
<circle cx="24" cy="21" r="6" fill="#ff5f56"/><circle cx="44" cy="21" r="6" fill="#ffbd2e"/><circle cx="64" cy="21" r="6" fill="#27c93f"/>
<text x="{W / 2}" y="26" text-anchor="middle" fill="{C['dim']}" font-size="12">azup — Azure toolbox manager</text>'''
demo = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
        f'font-family="{FONT}" font-size="{FS}">\n<title>azup demo</title>\n<defs>{"".join(defs)}</defs>\n'
        f'{chrome}\n{chr(10).join(body)}\n{chr(10).join(conf)}\n</svg>\n')
out = HERE / ("demo_snapshot.svg" if SNAP is not None else "demo.svg")
out.write_text(demo, encoding="utf-8")
print("wrote", out)

# ───────────────────────────── banner ─────────────────────────────
if SNAP is None:
    stars = "".join(
        f'<circle cx="{random.randint(330, 880)}" cy="{random.randint(12, 208)}" r="{random.choice([1, 1.4, 1.8])}" fill="#bfe6ff">'
        f'<animate attributeName="opacity" values="0.15;1;0.15" dur="{random.uniform(1.6, 3.6):.1f}s" begin="-{random.uniform(0, 3):.1f}s" repeatCount="indefinite"/></circle>'
        for _ in range(34))
    banner = f'''<svg xmlns="http://www.w3.org/2000/svg" width="900" height="230" viewBox="0 0 900 230">
<title>azup</title>
<defs>
 <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#06101f"/><stop offset="1" stop-color="#0b3a6b"/></linearGradient>
 <linearGradient id="g" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="340" y2="0" spreadMethod="reflect">
  <stop offset="0" stop-color="#0078d4"/><stop offset=".5" stop-color="#50e6ff"/><stop offset="1" stop-color="#0078d4"/>
  <animateTransform attributeName="gradientTransform" type="translate" from="0 0" to="680 0" dur="4s" repeatCount="indefinite"/>
 </linearGradient>
 <linearGradient id="body" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#50e6ff"/><stop offset="1" stop-color="#0078d4"/></linearGradient>
 <clipPath id="tag"><rect x="318" y="156" width="0" height="40"><animate attributeName="width" values="0;0;520;520" keyTimes="0;.1;.55;1" dur="8s" repeatCount="indefinite"/></rect></clipPath>
</defs>
<rect width="900" height="230" rx="18" fill="url(#bg)"/>
{stars}
<g>
 <animateTransform attributeName="transform" type="translate" values="0 0;0 -9;0 0" dur="3s" repeatCount="indefinite" calcMode="spline" keyTimes="0;.5;1" keySplines=".4 0 .6 1;.4 0 .6 1"/>
 <line x1="150" y1="52" x2="150" y2="72" stroke="#bfe6ff" stroke-width="3" stroke-linecap="round"/>
 <circle cx="150" cy="46" r="7" fill="#ffb900"><animate attributeName="r" values="5;8;5" dur="1.4s" repeatCount="indefinite"/></circle>
 <rect x="95" y="70" width="110" height="88" rx="26" fill="url(#body)"/>
 <rect x="105" y="82" width="90" height="56" rx="18" fill="#06101f"/>
 <ellipse cx="130" cy="106" rx="8" ry="9" fill="#fff"><animate attributeName="ry" values="9;9;9;1;9;9" keyTimes="0;.4;.45;.48;.52;1" dur="4s" repeatCount="indefinite"/></ellipse>
 <ellipse cx="170" cy="106" rx="8" ry="9" fill="#fff"><animate attributeName="ry" values="9;9;9;1;9;9" keyTimes="0;.4;.45;.48;.52;1" dur="4s" repeatCount="indefinite"/></ellipse>
 <circle cx="132" cy="108" r="3.5" fill="#06101f"><animate attributeName="cx" values="132;136;132;128;132" dur="6s" repeatCount="indefinite"/></circle>
 <circle cx="172" cy="108" r="3.5" fill="#06101f"><animate attributeName="cx" values="172;176;172;168;172" dur="6s" repeatCount="indefinite"/></circle>
 <path d="M140 127q10 9 20 0" stroke="#50e6ff" stroke-width="3" fill="none" stroke-linecap="round"/>
 <rect x="82" y="100" width="14" height="26" rx="7" fill="#0078d4"/><rect x="204" y="100" width="14" height="26" rx="7" fill="#0078d4"/>
</g>
<text x="316" y="128" font-family="'Segoe UI', -apple-system, 'Helvetica Neue', Arial, sans-serif" font-size="104" font-weight="800" fill="url(#g)" letter-spacing="-2">azup</text>
<g clip-path="url(#tag)"><text x="320" y="178" font-family="{FONT}" font-size="19" fill="#bfe6ff">install · update · done. no tears.</text></g>
<g font-family="{FONT}" font-size="13" fill="#7fc8ff" opacity="0"><animate attributeName="opacity" values="0;0;1;1" keyTimes="0;.45;.6;1" dur="8s" repeatCount="indefinite"/>
 <text x="320" y="208">Azure CLI · azd · Bicep · Az PowerShell · Graph · Entra · kubectl · Terraform</text></g>
</svg>
'''
    (HERE / "banner.svg").write_text(banner, encoding="utf-8")
    print("wrote", HERE / "banner.svg")
