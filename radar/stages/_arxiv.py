"""Fetch an arXiv paper's FULL TEXT (not just its abstract page) for grounded deep-read.

Why this exists: the P1 faithfulness eval caught that arXiv 详解 were written from the
abstract page only (~1.5K chars), so opus filled the gaps from its prior — definitions /
causal chains / background that aren't in the source, which the judge (correctly) flagged
as unsupported. Here we fetch the actual paper body so the 详解 is grounded in what the
paper really says.

Fallback chain — clean HTML first, full coverage last, and the caller's abstract as the
final safety net:
    arxiv.org/html/{id}  →  ar5iv.org/html/{id}  →  arxiv.org/pdf/{id} (pypdf)  →  ""
Both the `arxiv` source and the `hf_papers` source emit arxiv.org/abs/{id} URLs, so we key
off the URL/id, not the source name. Every step is best-effort: any failure falls through,
and "" means "no full text — use the abstract" (deepread's existing degrade path).
Returned text is truncated to max_chars (token discipline — full papers are large).
"""
from __future__ import annotations

import io
import re
from typing import Any, Optional

import requests

from ._article import _NL, _WS, _Extractor   # reuse the prose extractor (no import cycle: _article lazy-imports us)

# new-style 2606.26027(v1) and old-style cs/0503020(v2); from arxiv.org or huggingface.co/papers
_ARXIV_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf|html)/|huggingface\.co/papers/)"
    r"([a-z\-]+/\d{7}|\d{4}\.\d{4,5})(v\d+)?",
    re.I,
)
_VER = re.compile(r"v\d+$")
MIN_FULLTEXT = 4000   # a full-text attempt must beat this; abstract pages / stubs are ~1.5K
_UA = {"User-Agent": "agent-radar/0.1 (deepread; +grounded-eval)"}


def arxiv_id_from_url(url: str) -> Optional[str]:
    """arXiv id (with version if present) from an abs/pdf/html or HF-papers URL, else None."""
    m = _ARXIV_RE.search(url or "")
    return (m.group(1) + (m.group(2) or "")) if m else None


def _clean(text: str) -> str:
    return _NL.sub("\n\n", _WS.sub(" ", text)).strip()


def _extract_html(html: str) -> str:
    ex = _Extractor()
    try:
        ex.feed(html)
    except Exception:  # noqa: BLE001 — malformed html must not crash the run
        pass
    return _clean("".join(ex.parts))


def _try_html(session: requests.Session, url: str, timeout: float, proxies) -> str:
    try:
        r = session.get(url, timeout=timeout, proxies=proxies, headers=_UA)
        r.raise_for_status()
        # ar5iv (and arxiv.org/html for unconverted papers) 30x-redirects to the
        # arxiv.org/abs/ ABSTRACT page; its extract (~4-6K) passes MIN_FULLTEXT and would
        # masquerade as full text — silently skipping the pdf fallback (7.3 [3] 根因:
        # ar5iv/2607.02255 → 301/307 → abs, 4705 chars reported as src=ar5iv).
        if "/abs/" in (r.url or ""):
            return ""
        if "html" not in r.headers.get("content-type", "").lower():
            return ""
        return _extract_html(r.text)
    except Exception:  # noqa: BLE001 — fall through to the next source
        return ""


def _try_pdf(session: requests.Session, url: str, timeout: float, proxies) -> str:
    try:
        import pypdf
    except ImportError:
        return ""                       # PDF support optional — gracefully skip if absent
    try:
        r = session.get(url, timeout=timeout, proxies=proxies, headers=_UA)
        r.raise_for_status()
        if "pdf" not in r.headers.get("content-type", "").lower():
            return ""
        reader = pypdf.PdfReader(io.BytesIO(r.content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — one bad page shouldn't lose the paper
                continue
        return _clean("\n".join(parts))
    except Exception:  # noqa: BLE001
        return ""


def fulltext_with_source(arxiv_id: str, config: Any = None, *, timeout: float = 30.0,
                         max_chars: int = 30000) -> tuple[str, str]:
    """Return (text, source) where source ∈ {arxiv-html, ar5iv, pdf, ""}. "" text = all failed."""
    base = _VER.sub("", arxiv_id)       # base id resolves to the latest version, always valid
    session = requests.Session()
    proxies, trust_env = config.proxy_settings() if config is not None else (None, False)
    session.trust_env = trust_env

    for url, src in ((f"https://arxiv.org/html/{base}", "arxiv-html"),
                     (f"https://ar5iv.org/html/{base}", "ar5iv")):
        text = _try_html(session, url, timeout, proxies)
        if len(text) >= MIN_FULLTEXT:
            return text[:max_chars], src

    pdf = _try_pdf(session, f"https://arxiv.org/pdf/{base}", timeout, proxies)
    if len(pdf) >= MIN_FULLTEXT:
        return pdf[:max_chars], "pdf"
    return "", ""


def fetch_arxiv_fulltext(arxiv_id: str, config: Any = None, *, timeout: float = 30.0,
                         max_chars: int = 30000) -> str:
    """Best-effort arXiv full text via the fallback chain; "" if all sources fail."""
    return fulltext_with_source(arxiv_id, config, timeout=timeout, max_chars=max_chars)[0]
