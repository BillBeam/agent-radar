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


def test_send_disabled_when_secret_absent(monkeypatch, tmp_path):
    from radar.channels import web_reader as W
    monkeypatch.setattr(W.Paths, "web", tmp_path)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    monkeypatch.delenv("AGENT_RADAR_WEB_SECRET", raising=False)         # no secret → missing() blocks it
    ok = WebReaderChannel().send(Digest(date="2026-06-30", items=[], markdown="# x"), _ctx(_enabled()))
    assert ok is False
