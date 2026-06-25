"""Best-effort article main-text extraction (stdlib only) for grounded deep-read.

Grounding matters: the 详解 must be based on the real fetched text, not the model's
prior. We pull the main prose (p/h/li/blockquote/pre), skipping nav/script/footer.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

import requests

_WS = re.compile(r"[ \t]+")
_NL = re.compile(r"\n{3,}")


class _Extractor(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg"}
    KEEP = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre", "article", "main"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0
        self._keep = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.KEEP:
            self._keep += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self.KEEP:
            self._keep = max(0, self._keep - 1)
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip == 0 and self._keep > 0 and data.strip():
            self.parts.append(data)


def fetch_article_text(url: str, proxy: Optional[str] = None,
                       timeout: float = 25.0, max_chars: int = 8000) -> str:
    session = requests.Session()
    session.trust_env = bool(proxy)
    proxies = {"http": proxy, "https": proxy} if proxy else None
    r = session.get(url, timeout=timeout, proxies=proxies,
                    headers={"User-Agent": "agent-radar/0.1 (deepread)"})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
        return ""
    ex = _Extractor()
    try:
        ex.feed(r.text)
    except Exception:  # noqa: BLE001 — malformed html shouldn't crash the run
        pass
    text = "".join(ex.parts)
    text = _NL.sub("\n\n", _WS.sub(" ", text)).strip()
    return text[:max_chars]
