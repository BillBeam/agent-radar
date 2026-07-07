"""Shared design system for every Agent Radar web page (day reader / home / archive / stats).

One place owns: color tokens (light+dark), the type scale, the page chrome (header with
主页·归档·统计 nav, footer), and the shared component skins (cards, tables, code, buttons).
Page modules add only their own component CSS on top — so all pages read as ONE product.

Design direction (user-picked): 克制高级 — Linear/Stripe-docs restraint. Whitespace, hairline
borders instead of shadows, one accent. The single signature element is the radar identity:
a small pure-CSS radar mark (rotating sweep, reduced-motion → static) + mono "readout" meta
lines — truthful instrument telemetry, not decoration.

Fonts: Inter is fetched from Google Fonts as an ENHANCEMENT — the <link> is loaded async
(media="print" → "all" swap) so a blocked/offline network can never block rendering, and the
CJK text always rides the system stack (PingFang SC is already the premium CJK face on his
devices; a multi-MB CJK webfont would be weight without gain). No other external resource.
"""
from __future__ import annotations

import html as _html
from typing import Mapping, Optional

# Async, render-safe font load: browsers fetch print-media CSS without blocking paint;
# onload flips it live. If fonts.googleapis.com is unreachable (no proxy on the phone),
# the page simply keeps the system stack — degradation by design, zero layout jank (swap).
FONT_LINKS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" media="print" onload="this.media=\'all\'" '
    'href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">\n'
)

# ---- tokens + base + chrome + shared components -------------------------------------------
BASE_CSS = """
:root{color-scheme:light dark;
--bg:#FBFBFC;--surface:#F4F5F7;--surface2:#EDEFF2;--fg:#1A1C20;--muted:#5F6672;--faint:#8A919D;
--border:#E6E8EC;--hairline:#EEF0F3;--accent:#1257D0;--accent-ink:#0D47AB;
--data:#0E9384;--data-soft:rgba(14,147,132,.5);--data-faint:rgba(14,147,132,.12);
--qbg:#FFF8E3;--qbd:#E3B428;--ok:#188554;--bad:#C2413B;--zebra:#F8F9FA}
@media (prefers-color-scheme:dark){:root{
--bg:#0F1115;--surface:#171A20;--surface2:#1D2129;--fg:#E7E9EC;--muted:#9AA2AE;--faint:#6B7280;
--border:#262B34;--hairline:#20242C;--accent:#7FB0FF;--accent-ink:#93BDFF;
--data:#37B8A8;--data-soft:rgba(55,184,168,.55);--data-faint:rgba(55,184,168,.14);
--qbg:#2A2412;--qbd:#937A18;--ok:#4CC08A;--bad:#E2726C;--zebra:#14161B}}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
font-family:Inter,-apple-system,BlinkMacSystemFont,"PingFang SC","HarmonyOS Sans SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
font-size:17px;line-height:1.8;font-synthesis-weight:none}
main{max-width:720px;margin:0 auto;padding:8px 20px 72px}
h1{font-size:1.5rem;line-height:1.4;letter-spacing:-.01em;margin:.9em 0 .45em;font-weight:700}
h2{font-size:1.22rem;line-height:1.45;margin:2.2em 0 .7em;font-weight:700;letter-spacing:-.005em}
h3{font-size:1.1rem;margin:1.7em 0 .5em;font-weight:650}
p{margin:.75em 0}
a{color:var(--accent);text-decoration:none;overflow-wrap:anywhere}
a:hover,a:active{text-decoration:underline;text-underline-offset:3px}
ul{margin:.55em 0;padding-left:1.35em}
li{margin:.4em 0}
em{font-style:italic;color:var(--muted)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
.mono{font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace}
.readout{font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
font-size:.74rem;letter-spacing:.06em;color:var(--faint)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px}
blockquote{margin:.9em 0;padding:.6em 1em;background:var(--qbg);border-left:3px solid var(--qbd);
border-radius:6px;font-size:.95em}
.tbl{overflow-x:auto;margin:1em 0;border:1px solid var(--border);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:.88rem;line-height:1.55;min-width:460px;
font-variant-numeric:tabular-nums}
th,td{padding:.55em .7em;border-bottom:1px solid var(--hairline);text-align:left;vertical-align:top}
thead th{background:var(--surface);font-weight:650;font-size:.82rem;color:var(--muted);
letter-spacing:.02em;white-space:nowrap}
tbody tr:nth-child(even){background:var(--zebra)}
tbody tr:last-child td{border-bottom:none}
pre.code{background:var(--surface);border:1px solid var(--border);border-radius:10px;
padding:.85em 1em;overflow-x:auto;font-size:.83rem;line-height:1.6;margin:1em 0}
pre.code code{font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace}
/* site chrome */
header.site{border-bottom:1px solid var(--hairline)}
.site-in{max-width:720px;margin:0 auto;padding:14px 20px;display:flex;align-items:center;
justify-content:space-between;gap:12px}
.brand{display:inline-flex;align-items:center;gap:9px;color:var(--fg);text-decoration:none}
.brand:hover{text-decoration:none}
.brand-name{font-size:.78rem;font-weight:650;letter-spacing:.14em;color:var(--fg)}
nav.nav{display:flex;gap:18px;font-size:.88rem}
nav.nav a{color:var(--muted);font-weight:500}
nav.nav a:hover{color:var(--fg);text-decoration:none}
nav.nav a[aria-current]{color:var(--fg);font-weight:650}
footer.site-foot{max-width:720px;margin:0 auto;padding:20px 20px 40px;border-top:1px solid var(--hairline)}
footer.site-foot .foot-row{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
align-items:baseline}
footer.site-foot nav{display:flex;gap:14px;font-size:.82rem}
footer.site-foot nav a{color:var(--muted)}
/* radar mark (the one animated element; reduced-motion → static) */
.rmark{position:relative;width:18px;height:18px;border-radius:50%;display:inline-block;flex:none;
border:1.5px solid var(--data);overflow:hidden}
.rmark::before{content:"";position:absolute;inset:4px;border-radius:50%;
border:1px solid var(--data-soft)}
.rmark::after{content:"";position:absolute;left:50%;top:50%;width:3px;height:3px;border-radius:50%;
background:var(--data);transform:translate(-50%,-50%)}
.rmark .sweep{position:absolute;inset:0;border-radius:50%;
background:conic-gradient(var(--data-soft),transparent 90deg,transparent 360deg);
animation:rsweep 6s linear infinite}
@keyframes rsweep{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.rmark .sweep{animation:none}}
/* prev/next day */
.daynav{display:flex;justify-content:space-between;gap:10px;margin:2.6em 0 0;font-size:.92rem}
.daynav a{color:var(--muted);padding:.5em .85em;border:1px solid var(--border);border-radius:9px;
background:var(--surface)}
.daynav a:hover{color:var(--fg);border-color:var(--faint);text-decoration:none}
"""


def radar_mark() -> str:
    return '<span class="rmark" aria-hidden="true"><span class="sweep"></span></span>'


def _nav_html(nav: Optional[Mapping[str, str]], active: str) -> str:
    if not nav:
        return ""
    order = [("home", "主页"), ("archive", "归档"), ("stats", "统计")]
    links = []
    for key, label in order:
        url = nav.get(key)
        if not url:
            continue
        cur = ' aria-current="page"' if key == active else ""
        links.append(f'<a href="{_html.escape(url, quote=True)}"{cur}>{label}</a>')
    return f'<nav class="nav" aria-label="站内导航">{"".join(links)}</nav>' if links else ""


def page_shell(*, title: str, body: str, active: str = "",
               nav: Optional[Mapping[str, str]] = None,
               extra_css: str = "", extra_head: str = "", foot_note: str = "") -> str:
    """Full HTML document: head (noindex + async fonts) + chrome header/footer around `body`.
    `nav` maps home/archive/stats → URLs (relative "/{seg}/" — same origin, no host baked in);
    None/missing keys render no link (renderers stay pure & testable without segs)."""
    brand_href = (nav or {}).get("home", "#top")
    header = (
        '<header class="site" id="top"><div class="site-in">'
        f'<a class="brand" href="{_html.escape(brand_href, quote=True)}">{radar_mark()}'
        '<span class="brand-name">AGENT RADAR</span></a>'
        f"{_nav_html(nav, active)}"
        "</div></header>"
    )
    note = f'<span class="readout">{_html.escape(foot_note)}</span>' if foot_note else ""
    footer = (
        '<footer class="site-foot"><div class="foot-row">'
        '<span class="readout">AGENT RADAR · 私人前沿情报台</span>'
        f"{note}</div></footer>"
    )
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{_html.escape(title)}</title>\n"
        f"{FONT_LINKS}"
        f"{extra_head}"
        f"<style>{BASE_CSS}{extra_css}</style>\n"
        "</head>\n<body>\n"
        f"{header}\n<main>\n{body}\n</main>\n{footer}\n</body>\n</html>\n"
    )
