"""Render a digest's full markdown (`Digest.markdown`) → ONE self-contained HTML reading page —
the 4-axis 中文详解 delivered to the phone, a clickable page per day.

Mirrors `_docx_render.py`'s line parser (the exact constructs synthesize emits) but emits HTML with
inline CSS, so fidelity tracks the (validated) docx renderer WITHOUT a markdown dependency:
  # / ## / ###        → <h1>/<h2>/<h3>     (a `### [N] …` item gets id="item-N" + a TOC entry)
  a whole `**line**`  → <p class="axis">   (the ①②③④ axis sub-heads — must stay visible)
  inline **b** / *i*  → <strong> / <em>
  [text](url)         → <a href target=_blank>
  - / *  item         → <li> inside <ul>
  > quote             → <blockquote>        (e.g. the critic ⚠️可跳过 note)
  blank / ---         → list break / skipped

Content is NEVER regenerated — this only re-formats the already-rendered `Digest.markdown`. Every
page carries `<meta robots noindex>`; its URL is unguessable (see web_reader.py). No secrets here.
"""
from __future__ import annotations

import html as _html
import re

# one token = bold | italic | [text](url) — same grammar as _docx_render._INLINE
_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*].*?\*|\[[^\]]+\]\([^)]+\))")
_LINK = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD_LINE = re.compile(r"^\*\*(.+)\*\*$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_ITEM_HEAD = re.compile(r"^\[(\d+)\]\s+(.*)$")        # "[N] <rest>" — the per-item ### heading
_MD_LINK_SUB = re.compile(r"\[([^\]]+)\]\([^)]+\)")   # collapse [t](u) → t for the plain-text TOC


def _inline_html(text: str) -> str:
    """Inline **bold** / *italic* / [text](url) → HTML, escaping every plain piece (XSS-safe)."""
    out: list[str] = []
    for tok in _INLINE.split(text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            out.append(f"<strong>{_html.escape(tok[2:-2])}</strong>")
        elif (m := _LINK.match(tok)):
            href = _html.escape(m.group(2), quote=True)
            out.append(f'<a href="{href}" target="_blank" rel="noopener">{_html.escape(m.group(1))}</a>')
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            out.append(f"<em>{_html.escape(tok[1:-1])}</em>")
        else:
            out.append(_html.escape(tok))
    return "".join(out)


def _render_body(md: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Parse markdown lines → (html parts, toc entries [(N, plain '[N] title')])."""
    parts: list[str] = []
    toc: list[tuple[str, str]] = []
    bullets: list[str] = []

    def _flush() -> None:
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in md.split("\n"):
        line = raw.rstrip()
        if not line.strip() or line.strip() == "---":
            _flush()
            continue
        if (m := _BULLET.match(line)):
            bullets.append(_inline_html(m.group(1)))
            continue
        _flush()
        if (m := _HEADING.match(line)):
            level = min(len(m.group(1)), 6)
            text = m.group(2)
            mi = _ITEM_HEAD.match(text) if level == 3 else None
            if mi:
                n = mi.group(1)
                toc.append((n, _MD_LINK_SUB.sub(r"\1", text)))     # "[N] title" (link text only)
                parts.append(f'<h3 id="item-{n}" class="item">{_inline_html(text)}</h3>')
            else:
                parts.append(f"<h{level}>{_inline_html(text)}</h{level}>")
        elif (m := _BOLD_LINE.match(line)):                        # ①②③④ axis sub-heads
            parts.append(f'<p class="axis">{_inline_html(m.group(1))}</p>')
        elif (m := _QUOTE.match(line)):
            parts.append(f"<blockquote>{_inline_html(m.group(1))}</blockquote>")
        else:
            parts.append(f"<p>{_inline_html(line)}</p>")
    _flush()
    return parts, toc


def _toc_html(toc: list[tuple[str, str]]) -> str:
    lis = "".join(f'<li><a href="#item-{n}">{_html.escape(t)}</a></li>' for n, t in toc)
    return f'<nav class="toc" aria-label="目录"><div class="toc-h">目录 · 点标题跳到那篇</div><ul>{lis}</ul></nav>'


_CSS = """
:root{color-scheme:light dark;--fg:#1b1b1d;--muted:#6b7280;--bg:#fff;--card:#f6f7f9;
--border:#e6e7ea;--link:#0b66c3;--axis:#0b5cad;--qbg:#fff7e0;--qbd:#e9b400}
@media (prefers-color-scheme:dark){:root{--fg:#e7e8ea;--muted:#9aa0a6;--bg:#151619;--card:#1e2024;
--border:#2b2d33;--link:#66aaff;--axis:#8fbcff;--qbg:#2a2410;--qbd:#8a7300}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);-webkit-text-size-adjust:100%;
font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
font-size:17px;line-height:1.78}
main{max-width:720px;margin:0 auto;padding:22px 18px 120px}
h1{font-size:1.5rem;line-height:1.35;margin:.1em 0 .5em}
h2{font-size:1.2rem;margin:1.9em 0 .6em;padding-bottom:.3em;border-bottom:1px solid var(--border)}
h3{font-size:1.14rem;margin:1.6em 0 .5em}
h3.item{margin:2.1em 0 .5em;padding-top:1.15em;border-top:2px solid var(--border);scroll-margin-top:14px}
h3.item a{color:var(--fg)}
p{margin:.72em 0}
p.axis{font-weight:700;color:var(--axis);font-size:1.02rem;margin:1.35em 0 .25em}
a{color:var(--link);text-decoration:none;overflow-wrap:anywhere}
a:active,a:hover{text-decoration:underline}
ul{margin:.5em 0;padding-left:1.35em}
li{margin:.36em 0}
em{font-style:italic;color:var(--muted)}
blockquote{margin:.85em 0;padding:.55em .95em;background:var(--qbg);border-left:4px solid var(--qbd);
border-radius:5px}
.toc{background:var(--card);border:1px solid var(--border);border-radius:11px;padding:14px 16px;margin:1.5em 0}
.toc-h{font-weight:700;font-size:.9rem;color:var(--muted);margin-bottom:.5em}
.toc ul{list-style:none;margin:0;padding:0}
.toc li{margin:.32em 0;line-height:1.5}
.toc a{font-size:.98rem}
"""


def render_day_page(md: str, *, date: str = "") -> str:
    """`Digest.markdown` → a single noindex HTML page (TOC + per-item #item-N anchors). Pure."""
    parts, toc = _render_body(md)
    if toc:
        at = next((i for i, p in enumerate(parts) if p.startswith("<h2")), None)
        if at is None:
            at = next((i for i, p in enumerate(parts) if p.startswith("<h1")), -1) + 1
        parts.insert(at, _toc_html(toc))
    body = "\n".join(parts)
    title = _html.escape(f"Agent Radar · {date}" if date else "Agent Radar")
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{title}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n<main>\n"
        f"{body}\n"
        "</main>\n</body>\n</html>\n"
    )
