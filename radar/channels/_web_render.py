"""Render a digest's full markdown (`Digest.markdown`) → ONE self-contained HTML reading page —
the V5 教学级中文详解 delivered to the phone, a clickable page per day.

Mirrors `_docx_render.py`'s line parser for the shared constructs, and adds the V5 constructs
(tables / fenced code / mermaid diagrams). Emits HTML with inline CSS — no markdown dependency:
  # / ## / ###        → <h1>/<h2>/<h3>     (a `### [N] …` item gets id="item-N" + a TOC entry)
  a whole `**line**`  → <p class="axis">   (V5 section heads 🎯📖🔧🧪⚠️💡🔗 → class="axis sect")
  inline **b** / *i*  → <strong> / <em>
  [text](url)         → <a href target=_blank>
  - / *  item         → <li> inside <ul>
  > quote             → <blockquote>        (e.g. the critic ⚠️可跳过 note)
  | a | b | table     → <table> in a horizontally-scrollable wrapper (V5 实验数字)
  ```mermaid fence    → build-time SVG via the injected `mermaid_svg` callable; ANY failure
                        degrades that block to a plain <pre><code> — a bad diagram must never
                        break the page (other fence langs render as code directly)
  图：caption line     → <p class="caption">
  blank / ---         → list break / skipped

Content is NEVER regenerated — this only re-formats the already-rendered `Digest.markdown`. Every
page carries `<meta robots noindex>`; its URL is unguessable (see web_reader.py). No secrets here.
Pure except the injected `mermaid_svg` (the only construct that may touch a subprocess/cache).
"""
from __future__ import annotations

import html as _html
import re
from typing import Callable, Optional

# one token = bold | italic | [text](url) — same grammar as _docx_render._INLINE
_INLINE = re.compile(r"(\*\*.+?\*\*|\*[^*].*?\*|\[[^\]]+\]\([^)]+\))")
_LINK = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BOLD_LINE = re.compile(r"^\*\*(.+)\*\*$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_ITEM_HEAD = re.compile(r"^\[(\d+)\]\s+(.*)$")        # "[N] <rest>" — the per-item ### heading
_MD_LINK_SUB = re.compile(r"\[([^\]]+)\]\([^)]+\)")   # collapse [t](u) → t for the plain-text TOC
_FENCE = re.compile(r"^```\s*([\w+-]*)\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")
# V5 section heads (deepread.md 的七节) — visually promoted above plain bold sub-heads
# (⚠ appears twice: with and without the U+FE0F variation selector — models emit both)
_SECTION_EMOJI = ("🎯", "📖", "🔧", "🧪", "⚠️", "⚠", "💡", "🔗")
_READ_CHARS_PER_MIN = 400   # 中文技术文的粗略读速，只做目录里的时长估计


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


def _table_cells(row: str) -> list[str]:
    """Naive `|` split (good enough for the model's plain result tables — no escaped pipes)."""
    r = row.strip()
    if r.startswith("|"):
        r = r[1:]
    if r.endswith("|"):
        r = r[:-1]
    return [c.strip() for c in r.split("|")]


def _table_html(rows: list[str]) -> str:
    """Markdown table rows (header, separator, body…) → scrollable HTML table."""
    head = _table_cells(rows[0])
    body = [_table_cells(r) for r in rows[2:]]
    thead = "<tr>" + "".join(f"<th>{_inline_html(c)}</th>" for c in head) + "</tr>"
    trs = []
    for r in body:
        cells = (r + [""] * len(head))[: len(head)]           # pad/trim malformed rows to header width
        trs.append("<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in cells) + "</tr>")
    return (f'<div class="tbl"><table><thead>{thead}</thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')


def _fmt_read_stats(chars: int) -> str:
    mins = max(1, round(chars / _READ_CHARS_PER_MIN))
    if chars >= 1000:
        return f"{chars / 1000:.1f}千字·约{mins}分钟"
    return f"约{mins}分钟"


def _render_body(md: str, mermaid_svg: Optional[Callable[[str], Optional[str]]] = None,
                 ) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Parse markdown lines → (html parts, toc entries [(N, plain '[N] title', read-stats)])."""
    parts: list[str] = []
    toc: list[tuple[str, str, str]] = []
    bullets: list[str] = []
    lines = md.split("\n")
    item_chars: dict[str, int] = {}     # per-item visible-text volume → 目录的字数/时长
    cur_item: Optional[str] = None

    def _flush() -> None:
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    def _count(text: str) -> None:
        if cur_item is not None:
            item_chars[cur_item] = item_chars.get(cur_item, 0) + len(re.sub(r"\s", "", text))

    def _close_item() -> None:
        nonlocal cur_item
        if cur_item is not None:
            parts.append('<p class="backtop"><a href="#toc">↑ 返回目录</a></p>')
            cur_item = None

    i = 0
    while i < len(lines):
        line = lines[i].rstrip()

        if (m := _FENCE.match(line.strip())):                 # fenced block (```mermaid / ```lang)
            lang = m.group(1).lower()
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1                                            # past the closing fence (or EOF)
            _flush()
            code = "\n".join(block).strip()
            svg = None
            if lang == "mermaid" and mermaid_svg is not None:
                try:
                    svg = mermaid_svg(code)
                except Exception:  # noqa: BLE001 — a bad diagram must never break the page
                    svg = None
            if svg:
                parts.append(f'<figure class="diagram">{svg}</figure>')
            else:                                             # non-mermaid lang OR render failure
                parts.append(f'<pre class="code"><code>{_html.escape(code)}</code></pre>')
            continue

        if (line.strip().startswith("|") and i + 1 < len(lines)
                and _TABLE_SEP.match(lines[i + 1])):          # markdown table
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            _flush()
            parts.append(_table_html(rows))
            _count("".join(rows))
            continue

        i += 1
        if not line.strip() or line.strip() == "---":
            _flush()
            continue
        if (m := _BULLET.match(line)):
            bullets.append(_inline_html(m.group(1)))
            _count(m.group(1))
            continue
        _flush()
        if (m := _HEADING.match(line)):
            level = min(len(m.group(1)), 6)
            text = m.group(2)
            mi = _ITEM_HEAD.match(text) if level == 3 else None
            _close_item()                                     # any heading ends the previous item
            if mi:
                n = mi.group(1)
                cur_item = n
                toc.append((n, _MD_LINK_SUB.sub(r"\1", text), ""))   # stats filled after the pass
                parts.append(f'<h3 id="item-{n}" class="item">{_inline_html(text)}</h3>')
            else:
                parts.append(f"<h{level}>{_inline_html(text)}</h{level}>")
        elif (m := _BOLD_LINE.match(line)):                   # section heads + sub-heads
            text = m.group(1)
            cls = "axis sect" if text.startswith(_SECTION_EMOJI) else "axis"
            parts.append(f'<p class="{cls}">{_inline_html(text)}</p>')
            _count(text)
        elif (m := _QUOTE.match(line)):
            parts.append(f"<blockquote>{_inline_html(m.group(1))}</blockquote>")
            _count(m.group(1))
        elif line.startswith("图："):                          # diagram caption (V5)
            parts.append(f'<p class="caption">{_inline_html(line)}</p>')
            _count(line)
        else:
            parts.append(f"<p>{_inline_html(line)}</p>")
            _count(line)
    _flush()
    _close_item()
    toc = [(n, t, _fmt_read_stats(item_chars.get(n, 0))) for n, t, _ in toc]
    return parts, toc


def _toc_html(toc: list[tuple[str, str, str]]) -> str:
    lis = "".join(
        f'<li><a href="#item-{n}">{_html.escape(t)}</a><span class="mins">{_html.escape(s)}</span></li>'
        for n, t, s in toc)
    return (f'<nav class="toc" id="toc" aria-label="目录">'
            f'<div class="toc-h">目录 · 点标题跳到那篇</div><ul>{lis}</ul></nav>')


_CSS = """
:root{color-scheme:light dark;--fg:#1b1b1d;--muted:#6b7280;--bg:#fff;--card:#f6f7f9;
--border:#e6e7ea;--link:#0b66c3;--axis:#0b5cad;--qbg:#fff7e0;--qbd:#e9b400;--zebra:#fafbfc}
@media (prefers-color-scheme:dark){:root{--fg:#e7e8ea;--muted:#9aa0a6;--bg:#151619;--card:#1e2024;
--border:#2b2d33;--link:#66aaff;--axis:#8fbcff;--qbg:#2a2410;--qbd:#8a7300;--zebra:#1a1c20}}
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
p.axis.sect{font-size:1.08rem;margin:1.9em 0 .35em;padding-top:.85em;border-top:1px dashed var(--border)}
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
.toc .mins{color:var(--muted);font-size:.8rem;margin-left:.5em;white-space:nowrap}
.tbl{overflow-x:auto;margin:.9em 0;border:1px solid var(--border);border-radius:9px}
table{border-collapse:collapse;width:100%;font-size:.9rem;line-height:1.5;min-width:460px}
th,td{padding:.5em .65em;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
thead th{background:var(--card);font-weight:700}
tbody tr:nth-child(even){background:var(--zebra)}
tbody tr:last-child td{border-bottom:none}
pre.code{background:var(--card);border:1px solid var(--border);border-radius:9px;
padding:.8em .95em;overflow-x:auto;font-size:.84rem;line-height:1.55;margin:.9em 0}
pre.code code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
figure.diagram{margin:1em 0;padding:12px 8px;background:#fff;border:1px solid var(--border);
border-radius:10px;overflow-x:auto;text-align:center}
figure.diagram svg{max-width:100%;height:auto}
p.caption{color:var(--muted);font-size:.88rem;margin:.3em 0 1.1em;text-align:center}
p.backtop{margin:1.5em 0 .2em;text-align:right}
p.backtop a{font-size:.88rem;color:var(--muted)}
"""


def render_day_page(md: str, *, date: str = "",
                    mermaid_svg: Optional[Callable[[str], Optional[str]]] = None) -> str:
    """`Digest.markdown` → a single noindex HTML page (TOC + per-item #item-N anchors).
    `mermaid_svg` (optional) turns ```mermaid fences into inline SVG; None → code-block fallback."""
    parts, toc = _render_body(md, mermaid_svg)
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
