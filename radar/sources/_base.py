"""Shared base for source adapters: HTTP with proxy/timeout/retry/UA.

Robustness lives here so every adapter inherits timeouts + bounded retries.
Per-source circuit-breaking (熔断) is handled one level up in the fetch stage.

Rate-limit etiquette (2026-07-08): a 429/5xx means the server is telling us to
slow down. Retrying after 0.8s/1.6s (the old linear backoff) just re-trips the
limit and burns the source — that is exactly how arxiv (429) starved the pool to
76 candidates on 07-08. Transient/rate-limit statuses now honor Retry-After and
back off hard; plain transport errors (dead proxy) keep the cheap linear backoff
so a genuinely-down network still fails fast.
"""
from __future__ import annotations

import datetime
import json
import time
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import requests

from ..core.ports import SourceAdapter

USER_AGENT = "agent-radar/0.1 (personal frontier-tech digest; +https://github.com/)"

# Statuses that mean "transient / you're going too fast" — back off, don't hammer.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
# Escalating floor (seconds) when the server gives no Retry-After header.
_RATELIMIT_BACKOFF_S = (5.0, 15.0, 30.0)
# Cap any single wait so a hostile/huge Retry-After can't stall the whole run.
_RATELIMIT_WAIT_CAP_S = 120.0


def _retry_after_seconds(resp: Optional[requests.Response]) -> Optional[float]:
    """Parse a Retry-After header into a bounded delay, or None if absent/unparseable.

    Supports both forms in RFC 7231: delta-seconds ("120") and an HTTP-date.
    """
    if resp is None:
        return None
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    raw = raw.strip()
    try:                                      # delta-seconds form
        return min(max(float(int(raw)), 0.0), _RATELIMIT_WAIT_CAP_S)
    except ValueError:
        pass
    try:                                      # HTTP-date form
        when = parsedate_to_datetime(raw)
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        delta = (when - datetime.datetime.now(when.tzinfo)).total_seconds()
        return min(max(delta, 0.0), _RATELIMIT_WAIT_CAP_S)
    except (TypeError, ValueError):
        return None


class SourceError(Exception):
    pass


class BaseSource(SourceAdapter):
    def __init__(self, config: Any = None, log: Any = None):
        self.config = config
        self.log = log
        # Proxy is first-class: explicit config wins, else honor env (HTTPS_PROXY…).
        self._proxies, trust_env = config.proxy_settings() if config is not None else (None, False)
        self._session = requests.Session()
        self._session.trust_env = trust_env

    # -- HTTP --
    def _get(self, url: str, *, accept: Optional[str] = None,
             timeout: float = 20.0, retries: int = 2) -> requests.Response:
        headers = {"User-Agent": USER_AGENT}
        if accept:
            headers["Accept"] = accept
        last: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                r = self._session.get(url, headers=headers, proxies=self._proxies, timeout=timeout)
                r.raise_for_status()
                return r
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt >= retries:
                    break
                resp = getattr(e, "response", None)
                status = getattr(resp, "status_code", None)
                if status in _RETRY_STATUS:
                    # Rate-limited / transient server error: honor Retry-After, else
                    # escalate. A fast retry re-trips the limit (arxiv 429, 07-08).
                    wait = _retry_after_seconds(resp)
                    if wait is None:
                        wait = _RATELIMIT_BACKOFF_S[min(attempt, len(_RATELIMIT_BACKOFF_S) - 1)]
                else:
                    wait = 0.8 * (attempt + 1)  # transport/other: cheap linear backoff
                time.sleep(wait)
        raise SourceError(f"GET failed after {retries + 1} tries: {url} ({last!r})")

    def get_bytes(self, url: str, **kw: Any) -> bytes:
        return self._get(url, **kw).content

    def get_text(self, url: str, **kw: Any) -> str:
        return self._get(url, **kw).text

    def get_json(self, url: str, **kw: Any) -> Any:
        kw.setdefault("accept", "application/json")
        return json.loads(self._get(url, **kw).content)
