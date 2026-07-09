"""claude -p 的超时旋钮 —— 两个看门狗，生效值取 min，少设一个修复就被静默吃掉。

2.1.205 反编译（`strings` on the pinned binary）:
    fSi() = Math.max(Number(env.CLAUDE_STREAM_IDLE_TIMEOUT_MS) || 0, 300_000)   # 地板 300s
    mSi() = ... env.CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS ... clamp[10s, 30min]
    Io = fSi();  No = Math.min( mSi(En()), streamWatchdogOn ? Io : Infinity )

只设 BYTE_ 那个（07-06 的修复）→ mSi() 升到 1200s，随即被 Math.min 压回 Io=300s。
后果：每一次需要 >300s 的 opus 深读都死在 303.7s；trace 里**所有成功的 opus 调用都 <300s**。
本文件把「两个都设、都等于本次调用的 timeout」钉死——CLI 升级后若再回归，这里先红。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from radar.llm.claude_code import ClaudeCodeLLM

_IDLE_KNOBS = ("CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS", "CLAUDE_STREAM_IDLE_TIMEOUT_MS")


@pytest.fixture
def captured_env(monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen.update(kw.get("env") or {})
        return SimpleNamespace(returncode=0, stdout='{"result":"ok"}', stderr="")

    monkeypatch.setattr("radar.llm.claude_code.subprocess.run", fake_run)
    return seen


def test_both_idle_watchdogs_are_raised_to_our_timeout(captured_env):
    llm = ClaudeCodeLLM()
    llm._run("prompt", None, "opus", 1200.0)
    for knob in _IDLE_KNOBS:
        assert captured_env.get(knob) == "1200000", (
            f"{knob} 未对齐 → 生效值 = min(两者) 会被 300s 地板压回，>300s 的深读全死"
        )
    assert captured_env.get("API_TIMEOUT_MS") == "1200000"


def test_knobs_track_the_call_timeout_not_a_constant(captured_env):
    llm = ClaudeCodeLLM()
    llm._run("p", None, "haiku", 480.0)
    assert {captured_env[k] for k in _IDLE_KNOBS} == {"480000"}


def test_the_wrapper_stays_the_single_deadline_owner(captured_env):
    """两个旋钮必须彼此相等：不等 → Math.min 让实际期限不等于我们声明的 timeout。"""
    llm = ClaudeCodeLLM()
    llm._run("p", None, "sonnet", 900.0)
    a, b = (captured_env[k] for k in _IDLE_KNOBS)
    assert a == b


def test_api_key_never_reaches_the_child(captured_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")
    llm = ClaudeCodeLLM()
    llm._run("p", None, "haiku", 60.0)
    assert "ANTHROPIC_API_KEY" not in captured_env
