"""Weekly-review reading page — render the review markdown → one mobile-friendly HTML page,
leak-gate it, and deploy to the SAME Cloudflare Pages project as the daily reading pages.

Why: the summary push used to point at a local file path — on the phone that is a dead line
of text (the exact delivery lesson the daily 详解 already learned). The full weekly report now
gets the same treatment as the daily digest: a private reading page, and the push carries a
tappable link.

Privacy = the daily pages' tier, namespaced:
    seg = HMAC-SHA256(AGENT_RADAR_WEB_SECRET, "review-" + date)[:32]
unenumerable, stable per review date (re-runs land on the same URL), independent of the day
segs (the "review-" prefix keys a different HMAC input). Every page is noindex; data/web/ is
gitignored.

Red lines:
  * the page content passes the SAME leak_scan 口径 as committed artifacts BEFORE anything is
    written under data/web/site/ — a hit means no page this week (the push says so honestly);
    a flagged page must never even sit in the deploy dir, or the next daily deploy would ship it.
  * deploy failure only degrades the link — the push still goes out, the local report stays.
  * secrets stay in env (AGENT_RADAR_WEB_SECRET / CF creds); only the derived seg travels.
"""
from __future__ import annotations

import html as _html
import os
import re
from pathlib import Path
from typing import Optional

from ..channels._web_render import _CSS, _inline_html
from ..channels.web_reader import _seg, deploy_site
from ..core.config import Paths, RadarConfig
from ..core.io import atomic_write_text
from .leak_scan import scan_text

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_BOLD_LINE = re.compile(r"^\*\*(.+)\*\*$")
_TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")
_TABLE_SEP = re.compile(r"^\|[\s:|-]+\|$")

# on top of the day-page CSS: tables (the trend table) + a card around the at-a-glance block
_EXTRA_CSS = """
.tbl{overflow-x:auto;margin:.9em 0;border:1px solid var(--border);border-radius:9px}
table{border-collapse:collapse;width:100%;font-size:.9rem;line-height:1.55}
th,td{border-bottom:1px solid var(--border);padding:.5em .7em;text-align:left;vertical-align:top}
tr:last-child td{border-bottom:none}
th{background:var(--card);font-weight:600;white-space:nowrap}
.glance{background:var(--card);border:1px solid var(--border);border-radius:11px;
padding:4px 16px;margin:.9em 0 1.4em}
"""


def _flush_table(rows: list[str], parts: list[str]) -> None:
    if not rows:
        return
    body_rows = [r for r in rows if not _TABLE_SEP.match(r.replace(" ", ""))]
    has_head = len(body_rows) < len(rows) and body_rows  # a |---| separator row was present
    html_rows: list[str] = []
    for i, raw in enumerate(body_rows):
        cells = [c.strip() for c in raw.strip().strip("|").split("|")]
        tag = "th" if (has_head and i == 0) else "td"
        html_rows.append("<tr>" + "".join(f"<{tag}>{_inline_html(c)}</{tag}>" for c in cells) + "</tr>")
    parts.append('<div class="tbl"><table>' + "".join(html_rows) + "</table></div>")
    rows.clear()


def _render_body(md: str) -> list[str]:
    """Review markdown → HTML parts. Same line grammar as the day page (_web_render), plus
    | tables |, which the review's trend section needs and digests never emit."""
    parts: list[str] = []
    bullets: list[str] = []
    table: list[str] = []
    glance_at: Optional[int] = None       # index of the 一眼看完 heading, for the card wrap

    def _flush_bullets() -> None:
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    for raw in md.split("\n"):
        line = raw.rstrip()
        if _TABLE_ROW.match(line):
            _flush_bullets()
            table.append(line)
            continue
        _flush_table(table, parts)
        if not line.strip() or line.strip() == "---":
            _flush_bullets()
            continue
        if (m := _BULLET.match(line)):
            bullets.append(_inline_html(m.group(1)))
            continue
        _flush_bullets()
        if (m := _HEADING.match(line)):
            level = min(len(m.group(1)), 6)
            if level == 2 and glance_at is None and "一眼看完" in m.group(2):
                glance_at = len(parts)
            parts.append(f"<h{level}>{_inline_html(m.group(2))}</h{level}>")
        elif (m := _BOLD_LINE.match(line)):
            parts.append(f'<p class="axis">{_inline_html(m.group(1))}</p>')
        elif (m := _QUOTE.match(line)):
            parts.append(f"<blockquote>{_inline_html(m.group(1))}</blockquote>")
        else:
            parts.append(f"<p>{_inline_html(line)}</p>")
    _flush_bullets()
    _flush_table(table, parts)

    if glance_at is not None:             # wrap the at-a-glance paragraphs in a card
        end = glance_at + 1
        while end < len(parts) and not parts[end].startswith("<h2"):
            end += 1
        inner = "".join(parts[glance_at + 1:end])
        parts[glance_at + 1:end] = [f'<div class="glance">{inner}</div>']
    return parts


def render_review_page(md: str, *, date: str) -> str:
    """Review markdown → a single self-contained noindex HTML page (phone-first). Pure."""
    body = "\n".join(_render_body(md))
    title = _html.escape(f"Agent Radar 周报 · {date}")
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        f"<title>{title}</title>\n"
        f"<style>{_CSS}{_EXTRA_CSS}</style>\n"
        "</head>\n<body>\n<main>\n"
        f"{body}\n"
        "</main>\n</body>\n</html>\n"
    )


def publish_review(md: str, *, date: str, config: RadarConfig,
                   terms_file: Optional[Path] = None) -> tuple[Optional[str], str, str]:
    """Leak-gate → render → write data/web/site/{seg}/ → deploy. Returns (url, status, detail):
    status ∈ ok | disabled | missing | leak | render_failed | deploy_failed; url only on ok.
    Never raises — the weekly push must survive any failure here with an honest one-liner."""
    cfg = getattr(config.channels, "web_reader", None)
    if cfg is None:
        return None, "disabled", "channels.web_reader 未配置"
    missing = cfg.missing()
    if missing:
        return None, "missing", ",".join(missing)          # names only, never values

    hits, warn = scan_text(md, source=f"review-page:{date}", terms_file=terms_file)
    if hits:                                               # BEFORE any write — see module docstring
        return None, "leak", f"泄漏自检命中 {len(hits)} 处" + (f"（{warn}）" if warn else "")

    secret = os.environ.get("AGENT_RADAR_WEB_SECRET")
    if not secret:
        return None, "missing", "AGENT_RADAR_WEB_SECRET"
    seg = _seg(secret, f"review-{date}")
    del secret                                             # only the derived seg lives on

    try:
        atomic_write_text(Paths.web / "site" / seg / "index.html",
                          render_review_page(md, date=date))
    except Exception as e:  # noqa: BLE001
        return None, "render_failed", repr(e)[:160]

    r = cfg.resolved()
    ok, detail = deploy_site(r["project_name"])
    if not ok:
        return None, "deploy_failed", detail
    return f'{r["base_url"]}/{seg}/', "ok", (warn or "leak_scan 通过")
