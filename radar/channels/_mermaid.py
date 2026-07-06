"""Mermaid → static SVG at build time (mmdc via npx), content-hash cached.

Chosen path (probe 2026-07-06, decisions.md): `npx -y @mermaid-js/mermaid-cli` renders
headless (puppeteer Chromium, cached locally after first run) in seconds per diagram.
Build-time SVG keeps the reading page zero-JS / zero-external-request — the page stays a
single self-contained file, offline-readable, nothing to fetch at view time. The fallback
considered (same-origin mermaid.min.js + client render) is NOT wired: the probe passed.

Failure contract: ANY problem (npx missing, timeout, bad syntax, weird output) returns
None — the caller (_web_render) degrades that block to a plain code block. A bad diagram
must never break the daily page build.

Cache: data/web/mermaid-cache/<sha1(code)>.svg — idempotent redeploys of the same day
re-render zero diagrams; a changed diagram is a new hash. `--svgId mmd-<hash>` keeps the
SVG's internal CSS scoped per diagram (mmdc's default id collides across multiple inline
SVGs on one page). `-b transparent` + the page's white figure background = readable in
dark mode too.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from ..core.config import Paths

_TIMEOUT = 120   # generous: first call may fetch the npm package again after a cache purge
_CACHE_DIR = "mermaid-cache"


def mermaid_to_svg(code: str) -> Optional[str]:
    """Render one mermaid block to an inline-able SVG string, or None (→ code-block fallback)."""
    code = (code or "").strip()
    if not code:
        return None
    key = hashlib.sha1(code.encode("utf-8")).hexdigest()[:16]
    cache = Paths.web / _CACHE_DIR / f"{key}.svg"
    try:
        if cache.exists():
            return cache.read_text(encoding="utf-8") or None
    except OSError:
        pass
    npx = shutil.which("npx")
    if not npx:
        return None
    with tempfile.TemporaryDirectory(prefix="radar-mmd-") as td:
        src, out = Path(td) / "d.mmd", Path(td) / "d.svg"
        src.write_text(code + "\n", encoding="utf-8")
        try:
            proc = subprocess.run(
                [npx, "-y", "@mermaid-js/mermaid-cli", "-i", str(src), "-o", str(out),
                 "--svgId", f"mmd-{key}", "-b", "transparent", "-q"],
                capture_output=True, text=True, timeout=_TIMEOUT,
            )
        except Exception:  # noqa: BLE001 — timeout/OSError → fallback, never raise
            return None
        if proc.returncode != 0 or not out.exists():
            return None
        try:
            svg = out.read_text(encoding="utf-8")
        except OSError:
            return None
    if "<svg" not in svg[:300]:
        return None
    svg = svg[svg.index("<svg"):]              # drop any XML prolog — we inline into HTML
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(svg, encoding="utf-8")
    except OSError:
        pass                                    # cache is an optimization, not a requirement
    return svg
