"""Shared base for source adapters: HTTP with proxy/timeout/retry/UA.

Robustness lives here so every adapter inherits timeouts + bounded retries.
Per-source circuit-breaking (熔断) is handled one level up in the fetch stage.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

import requests

from ..core.ports import SourceAdapter

USER_AGENT = "agent-radar/0.1 (personal frontier-tech digest; +https://github.com/)"


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
                if attempt < retries:
                    time.sleep(0.8 * (attempt + 1))  # linear backoff
        raise SourceError(f"GET failed after {retries + 1} tries: {url} ({last!r})")

    def get_bytes(self, url: str, **kw: Any) -> bytes:
        return self._get(url, **kw).content

    def get_text(self, url: str, **kw: Any) -> str:
        return self._get(url, **kw).text

    def get_json(self, url: str, **kw: Any) -> Any:
        kw.setdefault("accept", "application/json")
        return json.loads(self._get(url, **kw).content)
