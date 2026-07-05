"""Weekly-review reading page (radar/self_improve/publish.py) — render fidelity
(tables / noindex / 一眼看完 card), the leak gate firing BEFORE any write or deploy,
seg namespacing vs the daily pages, and deploy-failure degradation. No real network:
deploy_site is monkeypatched at the publish module."""
from __future__ import annotations

from radar.channels import web_reader as W
from radar.core.config import Paths, RadarConfig
from radar.self_improve import publish as P

MD = (
    "# Agent Radar 周报 — 2026-07-05\n\n"
    "> 生成于 t。这份周报只做**观察与草案**。\n\n"
    "## 一眼看完\n\n🩺 运行：一切正常。\n\n🔍 详解质量：零幻觉。\n\n"
    "## 1. 详解质量走势（忠实度抽查）\n\n"
    "| 日期 | 忠实度 |\n|---|---|\n| 2026-07-05 | 100%（6/10 篇） |\n\n"
    "- 一个列表项\n"
)


def _cfg():
    return RadarConfig.model_validate(
        {"channels": {"web_reader": {"project_name": "agent-radar"}}})


def _env(monkeypatch):
    monkeypatch.setenv("AGENT_RADAR_WEB_SECRET", "s3cr3t")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "a")


def _terms(tmp_path):
    tf = tmp_path / "terms.txt"
    tf.write_text("ZZLEAKZZ\n", encoding="utf-8")
    return tf


def test_render_review_page_tables_noindex_glance():
    html = P.render_review_page(MD, date="2026-07-05")
    assert 'content="noindex' in html and 'lang="zh-CN"' in html
    assert "<title>Agent Radar 周报 · 2026-07-05</title>" in html
    assert "<table>" in html and "<th>日期</th>" in html and "<td>2026-07-05</td>" in html
    assert 'class="glance"' in html                    # 一眼看完 wrapped in a card
    assert "<li>一个列表项</li>" in html
    assert "<blockquote>" in html


def test_render_review_page_escapes_html():
    html = P.render_review_page("# T\n\n<script>alert(1)</script>\n", date="d")
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_publish_ok_writes_page_and_returns_stable_namespaced_url(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(Paths, "web", tmp_path / "web")
    calls = []
    monkeypatch.setattr(P, "deploy_site",
                        lambda project: calls.append(project) or (True, "deployed"))
    url, status, detail = P.publish_review(MD, date="2026-07-05", config=_cfg(),
                                           terms_file=_terms(tmp_path))
    assert status == "ok" and calls == ["agent-radar"]
    seg = W._seg("s3cr3t", "review-2026-07-05")
    assert url == f"https://agent-radar.pages.dev/{seg}/"
    page = (tmp_path / "web" / "site" / seg / "index.html").read_text(encoding="utf-8")
    assert "noindex" in page
    # review seg is namespaced away from the SAME day's daily seg (prefix in the HMAC input)
    assert seg != W._seg("s3cr3t", "2026-07-05")


def test_publish_leak_hit_no_write_no_deploy(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(Paths, "web", tmp_path / "web")
    calls = []
    monkeypatch.setattr(P, "deploy_site",
                        lambda project: calls.append(project) or (True, "deployed"))
    url, status, detail = P.publish_review(MD + "\n提到 ZZLEAKZZ 一次\n", date="2026-07-05",
                                           config=_cfg(), terms_file=_terms(tmp_path))
    assert url is None and status == "leak" and "1 处" in detail
    assert not calls                                   # deploy never attempted
    assert not (tmp_path / "web").exists()             # nothing landed in the deploy dir


def test_publish_deploy_failure_degrades(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(Paths, "web", tmp_path / "web")
    monkeypatch.setattr(P, "deploy_site", lambda project: (False, "boom"))
    url, status, detail = P.publish_review(MD, date="2026-07-05", config=_cfg(),
                                           terms_file=_terms(tmp_path))
    assert url is None and status == "deploy_failed" and detail == "boom"


def test_publish_unconfigured_and_missing_creds(tmp_path, monkeypatch):
    for k in ("AGENT_RADAR_WEB_SECRET", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
        monkeypatch.delenv(k, raising=False)
    url, status, _ = P.publish_review(MD, date="2026-07-05", config=RadarConfig())
    assert url is None and status == "disabled"
    url, status, detail = P.publish_review(MD, date="2026-07-05", config=_cfg())
    assert url is None and status == "missing" and "CLOUDFLARE_API_TOKEN" in detail
