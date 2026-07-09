"""Manual-run trigger — the Mac-side half of the home page's ⟳ 立即抓取 button.

Why it exists: the scheduled launchd daily fires whether or not the Mac is awake and on AC.
Three runs in a row (07-07 dark-wake, 07-08 battery slicing, 07-09 clamshell sleep) were cut
into fragments whose wake windows had no network — the pipeline degraded honestly and pushed
nothing. `caffeinate -s` is a documented no-op on battery, so software cannot fix that from
inside. The user's answer: stop trusting the clock, run it when he's actually at the machine.

Shape — deliberately the same as the web-vote loop (radar/serve/webvotes.py):
    page ⟳ → POST /trigger {seg} → Cloudflare KV → this daemon thread polls GET /trigger
    → spawns scripts/run-daily.sh → POST /trigger/state (queued→running→done/failed)
so the button is never a black box: the page reads the state back.

Guards against spending an opus deepread pass twice:
  1. worker-side cooldown (20 min) + in-flight rejection,
  2. this poller's persisted cursor — a KV read is eventually consistent (~60s), so a claimed
     request can still come back once; the cursor is written BEFORE the run is launched,
  3. the pipeline's own RunLock — the hard backstop if 1 and 2 are ever both wrong.

Network note: `run-serve.sh` strips every proxy var and exports NO_PROXY='*' (the DingTalk
Stream connection must not go through a Western proxy). run-daily.sh re-sources .env and gets
HTTPS_PROXY back — but NO_PROXY='*' would survive into the child and silently make `requests`
bypass that proxy for every host. `_child_env` drops it. Missing this turns a manual run into
the very "28 sources, 0 live" failure the button exists to prevent.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import signal
import subprocess
import threading
from typing import Any, Callable, Optional

import requests

from ..core.config import Paths
from ..core.io import atomic_write_json, read_json
from ..core.lock import is_held

INTERVAL_S = 25.0            # interactive: the user is watching the page
RUN_TIMEOUT_S = 3 * 60 * 60  # matches the worker's TRIGGER_STALE_MS
_CURSOR = Paths.state / "web_trigger_cursor.json"
_PROXY_BLOCKERS = ("NO_PROXY", "no_proxy")


def read_token(secret: str) -> str:
    """The Mac's bearer — derived, never the secret. Distinct from the vote-read token so a
    leaked read-only vote bearer can't spend quota."""
    return hmac.new(secret.encode(), b"trigger-read", hashlib.sha256).hexdigest()[:32]


def _child_env() -> dict:
    """serve's env minus NO_PROXY (see module docstring). run-daily.sh re-sources .env, so
    HTTPS_PROXY / DingTalk creds come back on their own; we only undo serve's own blocker."""
    return {k: v for k, v in os.environ.items() if k not in _PROXY_BLOCKERS}


def _run_summary() -> tuple[Optional[str], str]:
    """(run_id, human note) from the run the child just finished. Reads only non-secret
    fields — nothing from this note may leak a path, a token, or a proxy URL to the page."""
    lr = read_json(Paths.state / "last_run.json", {}) or {}
    delivered = lr.get("delivered") or {}
    sent = [k for k, v in delivered.items() if v]
    bits = [f"{lr.get('selected', '?')} 篇", f"深读 {lr.get('deepread_ok', '?')}"]
    bits.append("已投递 " + "、".join(sent) if sent else "未投递任何渠道")
    if lr.get("triage_degraded"):
        bits.append("triage 降级")
    return lr.get("run_id"), " · ".join(bits)


def run_daily(log: Any = None) -> tuple[bool, Optional[str], str]:
    """Spawn the same script launchd runs (network gate + caffeinate + eval chain included).
    Returns (ok, run_id, note).

    `start_new_session` puts the shell AND the python pipeline it spawns in one process group,
    so a timeout can kill the whole tree. `subprocess.run(timeout=…)` would only kill the shell
    and leave the pipeline orphaned — still holding the RunLock, still about to deliver.

    Output goes to a log file, never through a pipe we read back: stderr can carry the proxy URL
    (with credentials) and local paths, and none of that may reach the page via the state note.
    """
    script = Paths.root / "scripts" / "run-daily.sh"
    if not script.exists():
        return False, None, "scripts/run-daily.sh 不存在"
    logfile = Paths.state / "manual-run.log"
    try:
        Paths.state.mkdir(parents=True, exist_ok=True)
        with open(logfile, "a", encoding="utf-8") as fh:
            proc = subprocess.Popen([str(script)], cwd=str(Paths.root), env=_child_env(),
                                    stdout=fh, stderr=fh, start_new_session=True)
    except Exception as e:  # noqa: BLE001 — OSError etc; the poller must survive
        if log:
            log.warn("manual run failed to spawn", error=repr(e)[:120])
        return False, None, "没能启动管线（见 radar.log）"
    try:
        rc = proc.wait(timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _kill_tree(proc, log)
        return False, None, f"超过 {RUN_TIMEOUT_S // 3600} 小时未结束，已放弃"
    run_id, note = _run_summary()
    if rc != 0:
        return False, run_id, f"管线退出码 {rc} — 见 Mac 上的 radar.log"
    return True, run_id, note


def _kill_tree(proc: subprocess.Popen, log: Any = None) -> None:
    """SIGTERM the whole process group (shell + pipeline), then SIGKILL what survives."""
    for sig, grace in ((signal.SIGTERM, 20), (signal.SIGKILL, 5)):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            proc.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue
    if log:
        log.warn("manual run would not die", pid=proc.pid)


def _post_state(api_base: str, token: str, state: str, *, session: requests.Session,
                run_id: Optional[str] = None, note: str = "", log: Any = None) -> None:
    body: dict = {"state": state}
    if run_id:
        body["run_id"] = run_id
    if note:
        body["note"] = note
    try:
        session.post(f"{api_base}/trigger/state", json=body,
                     headers={"Authorization": f"Bearer {token}"}, timeout=20).raise_for_status()
    except Exception as e:  # noqa: BLE001 — a lost status update must never abort a real run
        if log:
            log.warn("trigger state post failed", state=state, error=repr(e)[:120])


def poll_once(api_base: str, token: str, *, log: Any = None,
              session: Optional[requests.Session] = None,
              runner: Callable[..., tuple[bool, Optional[str], str]] = run_daily) -> int:
    """One poll. Returns 1 if a run was launched (and has finished), 0 if nothing to do,
    -1 on a fetch failure (lets the caller back off while e.g. KV isn't provisioned)."""
    s = session or _session()
    try:
        r = s.get(f"{api_base}/trigger", headers={"Authorization": f"Bearer {token}"}, timeout=20)
        r.raise_for_status()
        req = (r.json() or {}).get("req")
    except Exception as e:  # noqa: BLE001
        if log:
            log.warn("trigger poll failed", error=repr(e)[:120])
        return -1
    if not req:
        return 0
    ts = int(req.get("ts") or 0)
    cursor = int((read_json(_CURSOR, {}) or {}).get("ts", 0))
    if ts <= cursor:
        return 0                                   # already claimed; KV just hasn't caught up
    atomic_write_json(_CURSOR, {"ts": ts})         # claim BEFORE running: a crash skips, never doubles

    if is_held(Paths.state / "run.lock"):
        if log:
            log.info("manual trigger ignored — a run already holds the lock")
        _post_state(api_base, token, "busy", session=s, log=log,
                    note="Mac 上已有一次运行在进行中，这次触发跳过")
        return 0

    if log:
        log.info("manual trigger claimed — starting daily", req_ts=ts)
    _post_state(api_base, token, "running", session=s, log=log, note="管线已启动")
    ok, run_id, note = runner(log=log)
    _post_state(api_base, token, "done" if ok else "failed", session=s, log=log,
                run_id=run_id, note=note)
    if log:
        log.info("manual run finished", ok=ok, run_id=run_id, note=note)
    return 1


def _session() -> requests.Session:
    s = requests.Session()
    proxy = os.environ.get("AGENT_RADAR_WEB_PROXY")   # preserved by run-serve.sh pre-strip
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
        s.trust_env = False
    return s


def start_poller(*, base_url: str, log: Any = None) -> Optional[threading.Thread]:
    """Spawn the daemon poll thread. Off (returns None) unless AGENT_RADAR_WEB_SECRET and a
    base_url are present — same discipline as the vote poller."""
    secret = os.environ.get("AGENT_RADAR_WEB_SECRET")
    if not secret or not base_url:
        if log:
            log.info("manual-trigger poller off (no base_url or web secret)")
        return None
    token = read_token(secret)
    del secret
    api = base_url.rstrip("/")
    session = _session()

    def _loop() -> None:
        # Same backoff shape as the vote poller: only the first failure of a streak is logged
        # (a 503 every 25s while KV isn't bound would flood radar.log for nothing).
        fails = 0
        while True:
            rc = poll_once(api, token, log=log if fails == 0 else None,
                           session=session, runner=run_daily)
            fails = fails + 1 if rc < 0 else 0
            threading.Event().wait(min(INTERVAL_S * (2 ** min(fails, 5)), 900.0))

    t = threading.Thread(target=_loop, name="manual-trigger-poller", daemon=True)
    t.start()
    if log:
        log.info("manual-trigger poller started", api=f"{api}/trigger", interval_s=INTERVAL_S)
    return t
