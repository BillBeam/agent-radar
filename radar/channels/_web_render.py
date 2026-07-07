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
import json as _json
import re
from typing import Callable, Mapping, Optional

from ._design import page_shell

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


def _vote_bar(item_id: str) -> str:
    return (f'<div class="vote" data-item="{_html.escape(item_id, quote=True)}">'
            '<span class="vote-q">这篇对你有用吗</span>'
            '<button class="vbtn" data-v="up">👍 有用</button>'
            '<button class="vbtn" data-v="down">👎 没用</button></div>')


def _render_body(md: str, mermaid_svg: Optional[Callable[[str], Optional[str]]] = None,
                 vote_ids: Optional[Mapping[str, str]] = None,
                 ) -> tuple[list[str], list[tuple[str, str, str]]]:
    """Parse markdown lines → (html parts, toc entries [(N, plain '[N] title', read-stats)]).
    `vote_ids` ([N] → item id) adds a 👍/👎 bar at each item's end — only when the page has
    a vote API to post to (see render_day_page)."""
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
            if vote_ids and cur_item in vote_ids:
                parts.append(_vote_bar(vote_ids[cur_item]))
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
                parts.append(f'<h3 id="item-{n}" class="item">'
                             f'<span class="idx">[{n}] </span>{_inline_html(mi.group(2))}</h3>')
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


READER_CSS = """
h1{font-size:1.42rem}
h2{padding-bottom:.35em;border-bottom:1px solid var(--hairline)}
h3.item{font-size:1.16rem;line-height:1.5;margin:2.4em 0 .5em;padding-top:1.4em;
border-top:1px solid var(--border);scroll-margin-top:14px}
h3.item a{color:var(--fg)}
h3.item a:hover{color:var(--accent)}
.item .idx{font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
color:var(--accent);font-size:.86em;font-weight:600;letter-spacing:.02em}
p.axis{font-weight:650;color:var(--accent-ink);font-size:1rem;margin:1.4em 0 .25em}
p.axis.sect{font-size:1.06rem;margin:2em 0 .4em;padding-top:.9em;border-top:1px dashed var(--hairline)}
.toc{background:var(--surface);border:1px solid var(--border);border-radius:12px;
padding:16px 18px;margin:1.6em 0}
.toc-h{font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
font-size:.72rem;letter-spacing:.08em;color:var(--faint);margin-bottom:.7em}
.toc ul{list-style:none;margin:0;padding:0}
.toc li{margin:.42em 0;line-height:1.55}
.toc a{font-size:.95rem;color:var(--fg)}
.toc a:hover{color:var(--accent)}
.toc .mins{color:var(--faint);font-size:.76rem;margin-left:.55em;white-space:nowrap;
font-family:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace}
figure.diagram{margin:1.1em 0;padding:14px 10px;background:#fff;border:1px solid var(--border);
border-radius:12px;overflow-x:auto;text-align:center}
figure.diagram svg{max-width:100%;height:auto}
p.caption{color:var(--muted);font-size:.86rem;margin:.35em 0 1.2em;text-align:center}
p.backtop{margin:1.4em 0 .2em;text-align:right}
p.backtop a{font-size:.85rem;color:var(--faint)}
.vote{display:flex;align-items:center;gap:10px;margin:1.7em 0 .3em;padding:.75em 1em;
background:var(--surface);border:1px solid var(--border);border-radius:12px;flex-wrap:wrap}
.vote-q{font-size:.88rem;color:var(--muted);margin-right:auto}
.vbtn{font:inherit;font-size:.9rem;padding:.4em .95em;border-radius:999px;cursor:pointer;
border:1px solid var(--border);background:var(--bg);color:var(--fg);transition:border-color .12s}
.vbtn:hover{border-color:var(--faint)}
.vbtn.on{border-color:var(--accent);color:var(--accent);font-weight:600}
.vbtn.busy{opacity:.55;pointer-events:none}
"""

# Vanilla, self-contained vote wiring — the ONLY script on the page, and only when a vote API
# exists. The page's own unguessable path segment rides along as the capability token; the
# Worker recomputes HMAC(secret, date) and rejects mismatches. localStorage keeps the pressed
# state per (date, item) so a revisit shows what was already voted. Votes may be changed
# (last-write-wins, same as `radar mark` / the DingTalk card).
_VOTE_JS = """
(function(){
var API=%(api)s,DATE=%(date)s,SEG=(location.pathname.split("/")[1]||"");
function k(id){return "ar-vote-"+DATE+"-"+id}
function mark(v,vote){v.querySelectorAll(".vbtn").forEach(function(b){
  b.classList.toggle("on",b.dataset.v===vote);b.classList.remove("busy")});
  v.querySelector(".vote-q").textContent=vote==="up"?"已记录：有用":"已记录：可跳过（票已进反馈）";}
document.querySelectorAll(".vote").forEach(function(v){
  var id=v.getAttribute("data-item");
  var saved=null;try{saved=localStorage.getItem(k(id))}catch(e){}
  if(saved)mark(v,saved);
  v.querySelectorAll(".vbtn").forEach(function(b){b.addEventListener("click",function(){
    b.classList.add("busy");
    fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({date:DATE,item_id:id,vote:b.dataset.v,seg:SEG})})
    .then(function(r){if(!r.ok)throw 0;
      try{localStorage.setItem(k(id),b.dataset.v)}catch(e){}
      mark(v,b.dataset.v)})
    .catch(function(){b.classList.remove("busy");
      v.querySelector(".vote-q").textContent="没发出去——网络原因，稍后再点一次"});
  })});
});
})();
"""


def render_day_page(md: str, *, date: str = "",
                    mermaid_svg: Optional[Callable[[str], Optional[str]]] = None,
                    nav: Optional[Mapping[str, str]] = None,
                    prev_day: Optional[tuple[str, str]] = None,
                    next_day: Optional[tuple[str, str]] = None,
                    vote_api: Optional[str] = None,
                    item_ids: Optional[Mapping[str, str]] = None) -> str:
    """`Digest.markdown` → a single noindex HTML page (TOC + per-item #item-N anchors).
    `mermaid_svg` (optional) turns ```mermaid fences into inline SVG; None → code-block fallback.
    `nav` (home/archive/stats URLs) draws the site chrome; `prev_day`/`next_day` are (url, label)
    for the bottom day-to-day walk; `vote_api` + `item_ids` ([N] → item id) enable 👍/👎 bars."""
    vote_ids = item_ids if (vote_api and item_ids) else None
    parts, toc = _render_body(md, mermaid_svg, vote_ids)
    if toc:
        at = next((i for i, p in enumerate(parts) if p.startswith("<h2")), None)
        if at is None:
            at = next((i for i, p in enumerate(parts) if p.startswith("<h1")), -1) + 1
        parts.insert(at, _toc_html(toc))
    if prev_day or next_day:
        left = (f'<a href="{_html.escape(prev_day[0], quote=True)}">← {_html.escape(prev_day[1])}</a>'
                if prev_day else "<span></span>")
        right = (f'<a href="{_html.escape(next_day[0], quote=True)}">{_html.escape(next_day[1])} →</a>'
                 if next_day else "<span></span>")
        parts.append(f'<nav class="daynav" aria-label="按天翻页">{left}{right}</nav>')
    body = "\n".join(parts)
    if vote_ids:
        body += "\n<script>" + _VOTE_JS % {"api": _json.dumps(vote_api),
                                           "date": _json.dumps(date)} + "</script>"
    return page_shell(title=f"Agent Radar · {date}" if date else "Agent Radar",
                      body=body, active="", nav=nav, extra_css=READER_CSS,
                      foot_note=f"{date} · 详解由 AI 生成，事实以原文为准" if date else "")
