"""LLM backend: Claude Code headless (`claude -p`) — uses your subscription.

Mechanism (the user's Q1): we shell out to `claude -p --output-format json`.
Because ANTHROPIC_API_KEY is unset and Claude Code is logged in via the
subscription, these calls draw on the subscription, not metered API billing. We
strip ANTHROPIC_API_KEY from the child env so it can never silently flip to API
billing. `--system-prompt` replaces Claude Code's heavy default prompt (cheaper,
focused); `--max-turns 1` forces a single deterministic completion (no tool loop).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any, Optional

from ..core.config import RadarConfig
from ..core.ports import LLMClient, LLMResult
from ..core.registry import register
from ._json import extract_json

_OVERLOAD_MARKERS = ("overloaded", "rate", "429", "529", "529 ")


@register("llm", "claude_code")
class ClaudeCodeLLM(LLMClient):
    def __init__(self, config: Optional[RadarConfig] = None, log: Any = None):
        self.config = config
        self.log = log
        self.bin = shutil.which("claude") or "claude"
        # cumulative token usage across the run (read by runner for last_run.json)
        self.usage_total = {"calls": 0, "input": 0, "output": 0,
                            "cache_read": 0, "cache_creation": 0}

    def _accumulate(self, usage: Optional[dict]) -> None:
        if not usage:
            return
        u = self.usage_total
        u["calls"] += 1
        u["input"] += usage.get("input_tokens", 0) or 0
        u["output"] += usage.get("output_tokens", 0) or 0
        u["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
        u["cache_creation"] += usage.get("cache_creation_input_tokens", 0) or 0

    def _run(self, prompt: str, system: Optional[str], model: str,
             timeout: float) -> tuple[bool, str, dict | None]:
        cmd = [self.bin, "-p", "--output-format", "json",
               "--model", model, "--max-turns", "1"]
        if system:
            cmd += ["--system-prompt", system]
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)  # force subscription, never API billing
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        if proc.returncode != 0:
            return False, f"exit {proc.returncode}: {(proc.stderr or '')[:240]}", None
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            # not the json envelope — treat raw stdout as the text
            return True, proc.stdout.strip(), None
        if data.get("is_error"):
            return False, str(data.get("subtype") or data.get("api_error_status") or "error"), data
        return True, data.get("result", ""), data

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        allow_tools: Optional[list[str]] = None,
        timeout: Optional[float] = None,
    ) -> LLMResult:
        model = model or "sonnet"
        timeout = timeout or 240.0
        last_err = "unknown"
        for attempt in range(3):
            try:
                ok, text, data = self._run(prompt, system, model, timeout)
            except subprocess.TimeoutExpired:
                last_err = "timeout"
                ok, text, data = False, "timeout", None
            except Exception as e:  # noqa: BLE001
                last_err = repr(e)
                ok, text, data = False, repr(e), None

            if ok:
                usage = (data or {}).get("usage")
                self._accumulate(usage)
                return LLMResult(text=text, raw=data, usage=usage, model=model, ok=True)

            last_err = text
            transient = any(m in text.lower() for m in _OVERLOAD_MARKERS) or text == "timeout"
            if self.log:
                self.log.warn("llm call failed", model=model, attempt=attempt,
                              transient=transient, error=text[:160])
            if not transient or attempt == 2:
                break
            time.sleep(2.0 * (attempt + 1))  # backoff on overload

        return LLMResult(text="", ok=False, error=last_err, model=model)

    # convenience for structured stages
    def complete_json(self, prompt: str, **kw: Any) -> tuple[Any, LLMResult]:
        res = self.complete(prompt, **kw)
        if not res.ok:
            return None, res
        try:
            return extract_json(res.text), res
        except ValueError as e:
            res.ok = False
            res.error = f"json parse: {e}"
            return None, res
