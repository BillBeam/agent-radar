"""手动触发 — 网页 ⟳ → KV → Mac 接单跑 daily。

守三条不可退的线：① 一次请求只花一次 opus（游标先写、RunLock 兜底、KV 重放不重跑）；
② 状态如实回报 queued→running→done/failed；③ 回报的 note 里绝不夹带路径/代理/凭据。
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import radar.serve.trigger as T
from radar.core.lock import is_held


def _wire(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(T, "_CURSOR", state / "web_trigger_cursor.json", raising=True)
    monkeypatch.setattr(T.Paths, "state", state, raising=True)
    return state


class _FakeSession:
    """GET /trigger returns `payload`; POST /trigger/state is recorded."""

    def __init__(self, payload=None, get_raises=False):
        self.payload = payload or {"ok": True, "state": {"state": "idle"}, "req": None}
        self.get_raises = get_raises
        self.posts: list[tuple[str, dict]] = []

    def get(self, url, **kw):
        if self.get_raises:
            raise OSError("net down")
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.payload)

    def post(self, url, **kw):
        self.posts.append((url, kw.get("json") or {}))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})

    @property
    def states(self) -> list[str]:
        return [b.get("state") for _, b in self.posts]


def _req(ts: int) -> dict:
    return {"ok": True, "state": {"state": "idle"}, "req": {"ts": ts}}


def _runner(ok=True, note="10 篇 · 深读 10", run_id="rid-1"):
    calls = []

    def r(**kw):
        calls.append(kw)
        return ok, run_id, note
    r.calls = calls          # type: ignore[attr-defined]
    return r


# ---- claiming ---------------------------------------------------------------------------

def test_no_pending_request_does_nothing(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    sess, run = _FakeSession(), _runner()
    assert T.poll_once("https://s.example", "tok", session=sess, runner=run) == 0
    assert run.calls == [] and sess.posts == []


def test_claim_runs_and_reports_running_then_done(tmp_path, monkeypatch):
    state = _wire(tmp_path, monkeypatch)
    sess, run = _FakeSession(_req(1700)), _runner()

    assert T.poll_once("https://s.example", "tok", session=sess, runner=run) == 1
    assert len(run.calls) == 1
    assert sess.states == ["running", "done"]
    assert sess.posts[-1][1]["run_id"] == "rid-1"
    assert sess.posts[-1][0].endswith("/trigger/state")
    # cursor persisted so the same request can never be claimed twice
    assert json.loads((state / "web_trigger_cursor.json").read_text()) == {"ts": 1700}


def test_replayed_request_is_ignored(tmp_path, monkeypatch):
    """KV is eventually consistent: a claimed request can come back for ~60s. It must not
    re-spend an opus pass — the cursor, not the server, is the authority."""
    state = _wire(tmp_path, monkeypatch)
    (state / "web_trigger_cursor.json").write_text(json.dumps({"ts": 1700}), encoding="utf-8")
    sess, run = _FakeSession(_req(1700)), _runner()
    assert T.poll_once("https://s.example", "tok", session=sess, runner=run) == 0
    assert run.calls == [] and sess.posts == []


def test_cursor_is_written_before_the_run_starts(tmp_path, monkeypatch):
    """A crash mid-run must skip the request, never double-spend it."""
    state = _wire(tmp_path, monkeypatch)
    cursor_at_run_time = {}

    def r(**kw):
        cursor_at_run_time.update(json.loads((state / "web_trigger_cursor.json").read_text()))
        return True, None, "ok"

    T.poll_once("https://s.example", "tok", session=_FakeSession(_req(99)), runner=r)
    assert cursor_at_run_time == {"ts": 99}


def test_run_lock_held_reports_busy_and_does_not_run(tmp_path, monkeypatch):
    state = _wire(tmp_path, monkeypatch)
    (state / "run.lock").write_text(json.dumps(
        {"pid": os.getpid(), "ts": "2999-01-01T00:00:00+00:00"}), encoding="utf-8")
    sess, run = _FakeSession(_req(1700)), _runner()

    assert T.poll_once("https://s.example", "tok", session=sess, runner=run) == 0
    assert run.calls == []
    assert sess.states == ["busy"]
    # still claimed: the request is consumed, not left to fire again on the next poll
    assert json.loads((state / "web_trigger_cursor.json").read_text()) == {"ts": 1700}


def test_runner_failure_reports_failed(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    sess = _FakeSession(_req(5))
    T.poll_once("https://s.example", "tok", session=sess,
                runner=_runner(ok=False, note="管线退出码 1 — 见 Mac 上的 radar.log"))
    assert sess.states == ["running", "failed"]
    assert "退出码" in sess.posts[-1][1]["note"]


def test_poll_survives_network_error(tmp_path, monkeypatch):
    _wire(tmp_path, monkeypatch)
    assert T.poll_once("https://s.example", "tok", session=_FakeSession(get_raises=True)) == -1


def test_lost_state_post_never_aborts_the_run(tmp_path, monkeypatch):
    """A dropped status update is cosmetic; the run must still happen and finish."""
    _wire(tmp_path, monkeypatch)

    class _PostBoom(_FakeSession):
        def post(self, url, **kw):
            raise OSError("status post died")

    run = _runner()
    assert T.poll_once("https://s.example", "tok", session=_PostBoom(_req(3)), runner=run) == 1
    assert len(run.calls) == 1


# ---- secrets / env ----------------------------------------------------------------------

def test_read_token_is_derived_and_distinct_from_the_vote_token():
    import hashlib
    import hmac as _hmac

    import radar.serve.webvotes as WV
    tok = T.read_token("s3cret")
    assert tok != "s3cret" and len(tok) == 32
    assert tok == _hmac.new(b"s3cret", b"trigger-read", hashlib.sha256).hexdigest()[:32]
    # a leaked read-only vote bearer must not be able to spend an opus pass
    assert tok != WV.read_token("s3cret")


def test_child_env_drops_no_proxy(monkeypatch):
    """run-serve.sh exports NO_PROXY='*' for the DingTalk stream. Inherited into the daily
    child it would silently make every source bypass HTTPS_PROXY → the exact 0-live-sources
    failure this button exists to avoid."""
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("DINGTALK_CLIENT_ID", "keep-me")
    env = T._child_env()
    assert "NO_PROXY" not in env and "no_proxy" not in env
    assert env["DINGTALK_CLIENT_ID"] == "keep-me"


def test_run_summary_note_carries_no_paths_or_secrets(tmp_path, monkeypatch):
    state = _wire(tmp_path, monkeypatch)
    (state / "last_run.json").write_text(json.dumps({
        "run_id": "20260709-daily-abcd", "selected": 10, "deepread_ok": 9,
        "delivered": {"web_reader": True, "dingtalk_card": True, "local": True, "macos": False},
    }), encoding="utf-8")
    run_id, note = T._run_summary()
    assert run_id == "20260709-daily-abcd"
    assert "10 篇" in note and "深读 9" in note and "web_reader" in note
    assert "/" not in note and "http" not in note


def test_run_summary_is_honest_when_nothing_was_delivered(tmp_path, monkeypatch):
    state = _wire(tmp_path, monkeypatch)
    (state / "last_run.json").write_text(json.dumps({
        "run_id": "r", "selected": 10, "deepread_ok": 4, "triage_degraded": True,
        "delivered": {"web_reader": False, "dingtalk_card": False, "local": True},
    }), encoding="utf-8")
    _, note = T._run_summary()
    assert "已投递 local" in note and "triage 降级" in note


# ---- the lock probe ---------------------------------------------------------------------

def test_is_held_reads_live_locks_only(tmp_path):
    lock = tmp_path / "run.lock"
    assert is_held(lock) is False                       # absent
    lock.write_text(json.dumps({"pid": os.getpid(), "ts": "2999-01-01T00:00:00+00:00"}))
    assert is_held(lock) is True                        # our own pid, not stale
    lock.write_text(json.dumps({"pid": 999_999_999, "ts": "2999-01-01T00:00:00+00:00"}))
    assert is_held(lock) is False                       # dead pid → reclaimable
    lock.write_text(json.dumps({"pid": os.getpid(), "ts": "2000-01-01T00:00:00+00:00"}))
    assert is_held(lock) is False                       # too old → stale


# ---- 锁窗口：真跑会跑很久，别把活着的跑判成僵死 -------------------------------------------

def test_stale_window_outlives_a_real_run():
    """RunLock 的年龄闸只是 PID 复用的兜底（崩溃的跑 PID 已死、立刻回收）。窗口必须长过一次
    健康的跑：V5 深读 10 篇 ≈ 75 分钟，07-08 被睡眠切碎那跑墙钟 4h33m。旧值 3600s 短于两者——
    手动触发的锁探针会在活跑上再起一个并发 daily（自审点名的「夺锁并发双投」）。"""
    from radar.core import lock as L
    assert L.STALE_AFTER_SECONDS >= 5 * 3600


def test_live_long_run_still_holds_the_lock(tmp_path):
    """一个已经跑了 2 小时的活进程仍必须持锁（旧的 3600s 会在这里放行第二次跑）。"""
    import json as _json
    from datetime import datetime, timedelta, timezone
    lock = tmp_path / "run.lock"
    two_h_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    lock.write_text(_json.dumps({"pid": os.getpid(), "ts": two_h_ago}))
    assert is_held(lock) is True


def test_timeout_kills_the_whole_process_group(monkeypatch, tmp_path):
    """subprocess.run(timeout=) 只杀 shell，python 管线会成为孤儿——继续持锁、继续投递。"""
    import subprocess as sp
    monkeypatch.setattr(T.Paths, "root", tmp_path, raising=True)
    monkeypatch.setattr(T.Paths, "state", tmp_path, raising=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "run-daily.sh").write_text("#!/bin/sh\nsleep 99\n")
    monkeypatch.setattr(T, "RUN_TIMEOUT_S", 3600, raising=True)

    killed: list[int] = []

    class _Proc:
        pid = 4242

        def wait(self, timeout=None):
            raise sp.TimeoutExpired("x", timeout or 0)

    monkeypatch.setattr(T.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(T.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(T.os, "killpg", lambda pgid, sig: killed.append(sig))

    ok, _, note = T.run_daily()
    assert ok is False and "已放弃" in note
    assert killed and killed[0] == T.signal.SIGTERM     # 组杀，不是只杀 shell
