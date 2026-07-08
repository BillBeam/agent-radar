"""web_reader channel — seg derivation, gating, missing()=names-only, deploy success/failure.
No real network: _deploy is monkeypatched. The web SECRET never appears in output (only derived seg)."""
from __future__ import annotations

from radar.channels.web_reader import WebReaderChannel, _seg
from radar.core.config import ChannelsConfig, RadarConfig, WebReaderConfig
from radar.core.models import Digest


def test_seg_stable_independent_unguessable():
    s = "dummy-secret-not-real"
    a = _seg(s, "2026-06-30")
    assert a == _seg(s, "2026-06-30")            # deterministic → same day = same URL (card retarget stable)
    assert a != _seg(s, "2026-07-01")            # per-day independent (one-way)
    assert a != _seg("other-secret", "2026-06-30")   # secret-dependent
    assert len(a) == 32 and all(c in "0123456789abcdef" for c in a)   # 128-bit unguessable hex


def test_is_enabled_requires_config():
    ch = WebReaderChannel()
    assert ch.is_enabled(RadarConfig()) is False                                     # no [channels.web_reader]
    on = RadarConfig(channels=ChannelsConfig(web_reader=WebReaderConfig(project_name="p")))
    assert ch.is_enabled(on) is True


def test_missing_reports_names_only(monkeypatch):
    for k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "AGENT_RADAR_WEB_SECRET",
              "CLOUDFLARE_PAGES_PROJECT", "AGENT_RADAR_WEB_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    miss = WebReaderConfig().missing()
    assert "project_name" in miss
    assert set(miss) >= {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "AGENT_RADAR_WEB_SECRET"}
    assert all("=" not in m for m in miss)       # NAMES only — never a value


def _log():
    return type("L", (), {"info": lambda *a, **k: None, "warn": lambda *a, **k: None})()


def _ctx(cfg):
    return type("Ctx", (), {"config": cfg, "log": _log(), "stats": {}})()


def _enabled():
    return RadarConfig(channels=ChannelsConfig(web_reader=WebReaderConfig(project_name="proj")))


def _creds(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.setenv("AGENT_RADAR_WEB_SECRET", "s3cr3t-dummy")
    monkeypatch.delenv("AGENT_RADAR_WEB_BASE_URL", raising=False)
    monkeypatch.delenv("CLOUDFLARE_PAGES_PROJECT", raising=False)


def test_send_sets_reader_url_on_deploy_success(monkeypatch, tmp_path):
    from radar.channels import web_reader as W
    monkeypatch.setattr(W.Paths, "web", tmp_path)                       # write pages under tmp
    _creds(monkeypatch)
    monkeypatch.setattr(W.WebReaderChannel, "_deploy", lambda self, project, ctx: True)

    ctx = _ctx(_enabled())
    md = "# 详解\n> meta\n## 🆕\n### [1] [T](http://u)\n正文"
    ok = WebReaderChannel().send(Digest(date="2026-06-30", items=[], markdown=md), ctx)
    seg = _seg("s3cr3t-dummy", "2026-06-30")
    assert ok is True
    assert ctx.stats["reader_url"] == f"https://proj.pages.dev/{seg}/"
    assert (tmp_path / "site" / seg / "index.html").exists()           # page written under the seg dir
    assert "s3cr3t-dummy" not in ctx.stats["reader_url"]               # SECRET never in the URL — only derived seg


def test_send_no_reader_url_on_deploy_failure(monkeypatch, tmp_path):
    from radar.channels import web_reader as W
    monkeypatch.setattr(W.Paths, "web", tmp_path)
    _creds(monkeypatch)
    monkeypatch.setattr(W.WebReaderChannel, "_deploy", lambda self, project, ctx: False)

    ctx = _ctx(_enabled())
    ok = WebReaderChannel().send(Digest(date="2026-06-30", items=[], markdown="# 详解\n正文"), ctx)
    assert ok is False
    assert "reader_url" not in ctx.stats                               # → card gracefully keeps arxiv link


def test_deploy_targets_production_branch(monkeypatch):
    """wrangler must deploy with --branch main (the production branch) → stable <project>.pages.dev,
    not a git-branch preview alias. Regression for the 404 where deploy went to Preview/master."""
    from radar.channels import web_reader as W
    captured = {}

    class _P:
        returncode, stdout, stderr = 0, "", ""

    def _run(argv, **kw):
        captured["argv"] = argv
        return _P()

    monkeypatch.setattr(W.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(W.subprocess, "run", _run)
    ok = W.WebReaderChannel()._deploy("agent-radar", _ctx(_enabled()))
    argv = captured["argv"]
    assert ok is True
    assert "pages" in argv and "deploy" in argv
    assert argv[argv.index("--project-name") + 1] == "agent-radar"
    assert argv[argv.index("--branch") + 1] == "main"     # production alias, not the master preview


def test_send_disabled_when_secret_absent(monkeypatch, tmp_path):
    from radar.channels import web_reader as W
    monkeypatch.setattr(W.Paths, "web", tmp_path)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.delenv("AGENT_RADAR_WEB_SECRET", raising=False)         # no secret → missing() blocks it
    ok = WebReaderChannel().send(Digest(date="2026-06-30", items=[], markdown="# x"), _ctx(_enabled()))
    assert ok is False


def test_deploy_strips_ambient_proxy_env(monkeypatch):
    """部署子进程必须剥掉环境代理——07-08 实测付费代理拖死 wrangler 上传（300s 超时），直连秒过；
    钉钉方向的「必须不走代理」在 CF 这里是「默认直连」。"""
    from radar.channels import web_reader as W
    monkeypatch.setenv("HTTPS_PROXY", "http://paid.example:1")
    monkeypatch.setenv("HTTP_PROXY", "http://paid.example:1")
    monkeypatch.delenv("AGENT_RADAR_DEPLOY_PROXY", raising=False)
    captured = {}

    class _P:
        returncode, stdout, stderr = 0, "", ""

    def _run(argv, **kw):
        captured["env"] = kw.get("env")
        return _P()

    monkeypatch.setattr(W.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(W.subprocess, "run", _run)
    ok, detail = W.deploy_site("agent-radar")
    assert ok is True and detail == "deployed"
    env = captured["env"]
    assert env is not None
    assert "HTTP_PROXY" not in env and "HTTPS_PROXY" not in env and "ALL_PROXY" not in env


def test_deploy_proxy_override_escape_hatch(monkeypatch):
    """AGENT_RADAR_DEPLOY_PROXY（如本机 7897）只作用于部署子进程——未来回到需代理的网络时的开关。"""
    from radar.channels import web_reader as W
    monkeypatch.setenv("HTTPS_PROXY", "http://paid.example:1")
    monkeypatch.setenv("AGENT_RADAR_DEPLOY_PROXY", "http://127.0.0.1:7897")
    captured = {}

    class _P:
        returncode, stdout, stderr = 0, "", ""

    def _run(argv, **kw):
        captured["env"] = kw.get("env")
        return _P()

    monkeypatch.setattr(W.shutil, "which", lambda _: "/usr/bin/npx")
    monkeypatch.setattr(W.subprocess, "run", _run)
    ok, _ = W.deploy_site("agent-radar")
    assert ok is True
    assert captured["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7897"
    assert captured["env"]["HTTP_PROXY"] == "http://127.0.0.1:7897"
