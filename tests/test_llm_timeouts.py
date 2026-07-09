"""claude -p 的超时旋钮 + CLI 版本钉死。

两个 idle 旋钮都是真的（`strings` on the binary）：生效期限 = `min(mSi() /* BYTE_ */,
fSi() /* 无 BYTE_，地板 300_000 */)`，只设一个另一个仍在钳制 —— 所以两个都设、都等于本次
调用的 timeout，让 wrapper 成为唯一的期限所有者。

⚠ 但它们**不是** 07-09 那个 300s 天花板的解药：实测把看门狗抬到 1200s、甚至整个关掉，
opus 仍在 ~301.2s 被 `Connection closed mid-response` 砍断。真根因是 CLI 2.1.205 的上游
回归，靠 `AGENT_RADAR_CLAUDE_BIN` 钉回 2.1.201 解决（见 decisions.md 07-09 的更正条目）。
本文件同时钉住那条纪律：wrapper 必须尊重这个 pin。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from radar.llm.claude_code import ClaudeCodeLLM

_IDLE_KNOBS = ("CLAUDE_BYTE_STREAM_IDLE_TIMEOUT_MS", "CLAUDE_STREAM_IDLE_TIMEOUT_MS")


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kw):
        seen["env"] = kw.get("env") or {}
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout='{"result":"ok"}', stderr="")

    monkeypatch.setattr("radar.llm.claude_code.subprocess.run", fake_run)
    return seen


def test_both_idle_knobs_are_set_and_equal(captured):
    """只设带 BYTE_ 的那个 → 另一个（地板 300s）仍在 min() 里钳制。"""
    ClaudeCodeLLM()._run("prompt", None, "opus", 1200.0)
    env = captured["env"]
    assert {env.get(k) for k in _IDLE_KNOBS} == {"1200000"}
    assert env.get("API_TIMEOUT_MS") == "1200000"


def test_knobs_track_the_call_timeout_not_a_constant(captured):
    ClaudeCodeLLM()._run("p", None, "haiku", 480.0)
    assert {captured["env"][k] for k in _IDLE_KNOBS} == {"480000"}


def test_api_key_never_reaches_the_child(captured, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-be-stripped")
    ClaudeCodeLLM()._run("p", None, "haiku", 60.0)
    assert "ANTHROPIC_API_KEY" not in captured["env"]


def test_pinned_cli_binary_is_honoured(monkeypatch, captured):
    """AGENT_RADAR_CLAUDE_BIN 是唯一挡住「brew 半夜换 CLI 打掉深读」的东西。
    2026-07-09: 2.1.205 把每个 >301s 的流式响应砍断；2.1.201 正常。"""
    monkeypatch.setenv("AGENT_RADAR_CLAUDE_BIN", "/pinned/claude")
    llm = ClaudeCodeLLM()
    assert llm.bin == "/pinned/claude"
    llm._run("p", None, "opus", 60.0)
    assert captured["cmd"][0] == "/pinned/claude"


def test_falls_back_to_path_when_unpinned(monkeypatch):
    monkeypatch.delenv("AGENT_RADAR_CLAUDE_BIN", raising=False)
    monkeypatch.setattr("radar.llm.claude_code.shutil.which", lambda _: "/usr/bin/claude")
    assert ClaudeCodeLLM().bin == "/usr/bin/claude"


def test_doctor_flags_the_broken_cli_version():
    """doctor 必须能认出 2.1.205 —— 这个版本静默吃掉了两天的 V5 详解。"""
    from radar.cli import _claude_version
    assert _claude_version(None) is None
    assert _claude_version("/definitely/not/a/binary") is None
