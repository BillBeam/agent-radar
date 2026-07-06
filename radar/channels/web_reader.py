"""Web reading-page channel — deliver the full 4-axis 中文详解 to the phone as ONE clickable page
per day (Cloudflare Pages); the voting card's per-row link points at `…/<seg>/#item-N`.

Why: deepread's full 详解 (`Digest.markdown`) only ever reached the local archive — never his phone.
Cards can't carry long text and DingTalk-doc write was too heavy/fragile, so a static reading page is
the 最稳 form: markdown renders with zero fidelity loss, every item has an anchor, phone+desktop both work.

Privacy (chosen = B: unguessable + noindex, zero login). The day page lives at
    https://<project>.pages.dev/<seg>/       seg = HMAC-SHA256(AGENT_RADAR_WEB_SECRET, date)[:32]
→ unenumerable (no secret ⇒ no seg), STABLE per day (re-runs & the card retarget hit the same URL),
per-day-independent (sharing one day never leaks another). Every page is `<meta robots noindex>` and
`data/web/site/` is gitignored (never in the public repo).

SECRET RULE (hard): `AGENT_RADAR_WEB_SECRET` is read from env ONLY — never generated, never logged,
never written to any file/log/decisions. Only the derived `seg` (a capability token that legitimately
travels to his phone inside the URL) is ever emitted. Deploy goes over the PUBLIC internet (unlike the
domestic DingTalk channels — no `trust_env` fiddling); CF creds ride the inherited env, never on argv.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import subprocess
from typing import Any

from ..core.config import Paths
from ..core.io import atomic_write_text
from ..core.models import Digest, RunContext
from ..core.ports import Channel
from ..core.registry import register
from ._mermaid import mermaid_to_svg
from ._web_render import render_day_page

_DEPLOY_TIMEOUT = 300   # first npx run fetches wrangler; generous ceiling


def _seg(secret: str, date: str) -> str:
    """Per-day unguessable path segment: deterministic (⇒ stable URL) + one-way (⇒ days independent).
    Callers never store/log `secret`; only this derived value leaves the function.
    The weekly review page reuses this with a "review-YYYY-MM-DD" key — the prefix namespaces
    review segs away from day segs under the same secret."""
    return hmac.new(secret.encode(), date.encode(), hashlib.sha256).hexdigest()[:32]


def deploy_site(project: str) -> tuple[bool, str]:
    """One `npx wrangler pages deploy` of the whole data/web/site dir → the project's PRODUCTION
    alias (--branch main = the production branch set at create; a repo on `master` would otherwise
    land on a Preview alias — the 404 lesson). CF creds are read by wrangler from the inherited env
    (CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID) — never on argv, never logged.
    Shared by the daily channel and the weekly-review publisher; returns (ok, detail)."""
    npx = shutil.which("npx")
    if not npx:
        return False, "npx not found"
    try:
        proc = subprocess.run(
            [npx, "-y", "wrangler", "pages", "deploy", str(Paths.web / "site"),
             "--project-name", project, "--branch", "main", "--commit-dirty=true"],
            cwd=str(Paths.root), capture_output=True, text=True, timeout=_DEPLOY_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 — timeout / OSError
        return False, repr(e)[:160]
    if proc.returncode != 0:
        return False, f"code={proc.returncode} {(proc.stderr or '').strip()[-280:]}"
    return True, "deployed"


@register("channel", "web_reader")
class WebReaderChannel(Channel):
    name = "web_reader"

    def is_enabled(self, config: Any) -> bool:
        return config.channels.web_reader is not None

    def send(self, digest: Digest, ctx: RunContext) -> bool:
        cfg = ctx.config.channels.web_reader
        if cfg is None:
            return False
        missing = cfg.missing()
        if missing:
            ctx.log.warn("web_reader disabled — missing config/creds", missing=missing,
                         hint="env CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID + AGENT_RADAR_WEB_SECRET; project_name in config")
            return False
        secret = os.environ.get("AGENT_RADAR_WEB_SECRET")   # env ONLY — never stored/logged
        if not secret:
            return False
        seg = _seg(secret, digest.date)
        del secret                                          # drop the reference once seg is derived
        r = cfg.resolved()                                  # non-secret ids only (no token, no web secret)

        try:
            html = render_day_page(digest.markdown, date=digest.date, mermaid_svg=mermaid_to_svg)
            atomic_write_text(Paths.web / "site" / seg / "index.html", html)
        except Exception as e:  # noqa: BLE001
            ctx.log.warn("web_reader render/write failed", error=repr(e)[:160])
            return False

        if not self._deploy(r["project_name"], ctx):
            return False
        # seg is a capability token (not the secret) — it legitimately rides the URL to his phone.
        ctx.stats["reader_url"] = f'{r["base_url"]}/{seg}/'
        ctx.log.info("web_reader deployed", project=r["project_name"], date=digest.date)
        return True

    def _deploy(self, project: str, ctx: RunContext) -> bool:
        """Any failure → False (caller leaves `reader_url` unset → the card keeps the arxiv link)."""
        ok, detail = deploy_site(project)
        if not ok:
            ctx.log.warn("web_reader: deploy failed (card falls back to arxiv)", detail=detail)
        return ok
