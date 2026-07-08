"""LLM backend: Claude Code headless (`claude -p`) — uses your subscription.

Mechanism (the user's Q1): we shell out to `claude -p --output-format json`.
Because ANTHROPIC_API_KEY is unset and Claude Code is logged in via the
subscription, these calls draw on the subscription, not metered API billing. We
strip ANTHROPIC_API_KEY from the child env so it can never silently flip to API
billing. `--system-prompt` replaces Claude Code's heavy default prompt (cheaper,
focused); `--tools ""` removes every tool so the model can only answer with text
(a stray opening tool call would burn the whole call, see _run); `--max-turns 1`
then forces a single deterministic completion.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from ..core.config import RadarConfig
from ..core.ports import LLMClient, LLMResult
from ..core.registry import register
from ._json import extract_json

_OVERLOAD_MARKERS = ("overloaded", "rate", "429", "529", "529 ",
                     # genuine mid-stream server FINs (claude-code#67766 et al.) — retry-worthy.
                     # Visible to us since 07-08 (failure diag now includes CLI stdout).
                     "connection closed", "internal server")

# claude -p auto-loads CLAUDE.md from its cwd AND every ancestor directory into model
# context — `--system-prompt` does NOT suppress that. Run from inside the repo, and every
# pipeline call silently carried the gitignored, identity-laden project manual (and any
# ancestor CLAUDE.md above the repo) straight into triage/rerank/critic/deepread context.
# That bled into outputs bound for the PUBLIC reading page (the 2026-06-30 ④ employer
# mention and the V3 probe leak were context bleed, not model prior — the identity guard
# was the only thing holding). Every call therefore runs from a neutral per-user tmp dir
# whose ancestry (/var/folders/… or /tmp) carries no CLAUDE.md. Personalization must flow
# ONLY through the sanctioned channel (USER.md → rerank preamble), never via cwd accident.
_NEUTRAL_CWD = Path(tempfile.gettempdir()) / "agent-radar-llm-cwd"


@register("llm", "claude_code")
class ClaudeCodeLLM(LLMClient):
    def __init__(self, config: Optional[RadarConfig] = None, log: Any = None,
                 trace: Any = None):
        self.config = config
        self.log = log
        self.trace = trace   # optional Tracer → per-LLM-call events (set by the runner)
        # AGENT_RADAR_CLAUDE_BIN (.env, machine-local) pins the pipeline to a known-good
        # CLI build, decoupled from brew/interactive upgrades. Born 07-08: brew bumped the
        # CLI to 2.1.204 mid-day and its new request shape left big-payload opus calls
        # byte-silent >300s (server never streams) → watchdog kill → 9/9 deepreads dead;
        # the pre-update CLI on the SAME payloads streamed instantly all morning.
        self.bin = os.environ.get("AGENT_RADAR_CLAUDE_BIN") or shutil.which("claude") or "claude"
        # cumulative token usage across the run (read by runner for last_run.json)
        self.usage_total = {"calls": 0, "input": 0, "output": 0,
                            "cache_read": 0, "cache_creation": 0}
        # per-stage (tag) roll-up: {tag: {calls,input,output,ms,model}} for last_run.json
        self.by_stage: dict[str, dict] = {}

    def _record_call(self, model: str, tag: Optional[str], ms: float,
                     usage: Optional[dict], error: Optional[str] = None) -> None:
        """Per-call observability: roll up (tag → tokens/latency) for last_run.json + emit a
        per-call trace event. Failed ATTEMPTS are recorded too (`failed` counter + an
        `error` field on the event) — 7.3's rerank burned 726s on 3 timeouts and was
        invisible in both trace and by_stage. Best-effort — must never break a call."""
        u = usage or {}
        ino, out = u.get("input_tokens", 0) or 0, u.get("output_tokens", 0) or 0
        s = self.by_stage.setdefault(tag or "?", {"calls": 0, "input": 0, "output": 0,
                                                  "ms": 0.0, "model": model})
        if error is None:
            s["calls"] += 1
            s["input"] += ino
            s["output"] += out
        else:
            s["failed"] = s.get("failed", 0) + 1
        s["ms"] = round(s["ms"] + ms, 1)
        if self.trace is not None:
            try:
                fields = dict(tag=tag, model=model, ms=ms, input=ino, output=out,
                              cache_read=u.get("cache_read_input_tokens", 0) or 0)
                if error is not None:
                    fields["error"] = error
                self.trace.event("llm_call", **fields)
            except Exception:  # noqa: BLE001 — tracing must never break a call
                pass

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
        # --tools "": pipeline calls are strictly text-in-text-out. With the default tool
        # set available, the model sporadically opens with a tool call (seen live
        # 2026-07-04: sonnet answered a rerank prompt with ReportFindings) — that burns
        # the single allowed turn, the CLI exits 1 with EMPTY stderr (max_turns_reached),
        # and the wrapper reads it as a non-transient failure → silent stage degrade.
        # No tools also closes the residual contamination channel (a pipeline call must
        # never Read files / browse; personalization flows only via USER.md → preamble).
        cmd = [self.bin, "-p", "--output-format", "json", "--tools", "",
               "--model", model, "--max-turns", "1"]
        if system:
            cmd += ["--system-prompt", system]
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)  # force subscription, never API billing
        # CLI 2.1.204's byte watchdog aborts at 300s of stream silence — and heavy prompts
        # (80K grounding) leave opus silent PAST that before its first byte → "API Error:
        # Connection closed mid-response" (string lives in the CLI binary; killed all 9
        # fresh deepreads on 07-08). Var names verified via `strings` on the binary —
        # docs/blogs circulate a wrong name without BYTE_. Align both knobs with OUR call
        # timeout so this wrapper stays the single deadline owner.
        env["CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS"] = str(int(timeout * 1000))
        env["API_TIMEOUT_MS"] = str(int(timeout * 1000))
        env["DISABLE_AUTOUPDATER"] = "1"   # a pinned binary must never self-update mid-run
        _NEUTRAL_CWD.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            timeout=timeout, env=env,
            cwd=str(_NEUTRAL_CWD),   # no CLAUDE.md in this dir or any ancestor (see above)
        )
        if proc.returncode != 0:
            # The CLI often puts the real diagnostic on STDOUT (json envelope subtype:
            # max_turns / usage-limit text) and exits with EMPTY stderr — dropping stdout
            # here left 07-08's 9-item deepread wipeout as an undiagnosable `exit 1: `.
            diag = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            return False, f"exit {proc.returncode}: {diag[:240]}", None
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
        timeout: Optional[float] = None,
        retries: int = 3,
        tag: Optional[str] = None,
    ) -> LLMResult:
        model = model or "sonnet"
        timeout = timeout or 240.0
        retries = max(1, retries)
        last_err = "unknown"
        for attempt in range(retries):
            t0 = time.monotonic()
            try:
                ok, text, data = self._run(prompt, system, model, timeout)
            except subprocess.TimeoutExpired:
                last_err = "timeout"
                ok, text, data = False, "timeout", None
            except Exception as e:  # noqa: BLE001
                last_err = repr(e)
                ok, text, data = False, repr(e), None
            ms = round((time.monotonic() - t0) * 1000, 1)

            if ok:
                usage = (data or {}).get("usage")
                self._accumulate(usage)
                self._record_call(model, tag, ms, usage)
                return LLMResult(text=text, raw=data, usage=usage, model=model, ok=True)

            self._record_call(model, tag, ms, None, error=(text or "?")[:100])
            last_err = text
            # `exit N:` with EMPTY stderr is the CLI dying without a diagnostic (seen live
            # 2026-07-06: one opus deepread call under V5 load) — undiagnosable ⇒ worth the
            # retry, losing a 详解 to a one-off CLI hiccup costs more than one extra attempt.
            undiagnosed_exit = bool(re.fullmatch(r"exit \d+:\s*", text or ""))
            transient = (any(m in text.lower() for m in _OVERLOAD_MARKERS)
                         or text == "timeout" or undiagnosed_exit)
            if self.log:
                self.log.warn("llm call failed", model=model, attempt=attempt,
                              transient=transient, error=text[:160])
            if not transient or attempt == retries - 1:
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
