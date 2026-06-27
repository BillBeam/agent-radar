"""arXiv full-text fetch — id recognition, fallback chain, truncation, routing. No network."""
from __future__ import annotations

import radar.stages._arxiv as A
import radar.stages._article as ART


# ---- id recognition (covers arxiv source + hf_papers source URL shapes) ----
def test_arxiv_id_from_url():
    f = A.arxiv_id_from_url
    assert f("https://arxiv.org/abs/2606.26027") == "2606.26027"
    assert f("https://arxiv.org/abs/2606.27243v1") == "2606.27243v1"     # version kept
    assert f("https://arxiv.org/pdf/2401.12345") == "2401.12345"
    assert f("https://arxiv.org/html/2402.08954v2") == "2402.08954v2"
    assert f("https://huggingface.co/papers/2606.26027") == "2606.26027"  # HF papers → arXiv id
    assert f("https://arxiv.org/abs/cs/0503020") == "cs/0503020"          # old-style id
    assert f("https://www.anthropic.com/engineering/x") is None           # non-arXiv
    assert f("") is None


# ---- fallback chain: html → ar5iv → pdf → "" , with the MIN_FULLTEXT gate ----
def test_chain_prefers_arxiv_html(monkeypatch):
    seen = []

    def fake_html(s, u, t, p):
        seen.append(u)
        return "X" * 5000 if "arxiv.org/html" in u else ""
    monkeypatch.setattr(A, "_try_html", fake_html)
    monkeypatch.setattr(A, "_try_pdf", lambda *a: "P" * 9999)   # must NOT be reached
    text, src = A.fulltext_with_source("2606.26027", config=None, max_chars=30000)
    assert src == "arxiv-html" and len(text) == 5000
    assert seen[0].endswith("/html/2606.26027")


def test_chain_falls_to_ar5iv(monkeypatch):
    monkeypatch.setattr(A, "_try_html",
                        lambda s, u, t, p: "A" * 6000 if "ar5iv" in u else "short")
    monkeypatch.setattr(A, "_try_pdf", lambda *a: "")
    text, src = A.fulltext_with_source("2606.26027", config=None)
    assert src == "ar5iv" and len(text) == 6000


def test_chain_falls_to_pdf(monkeypatch):
    monkeypatch.setattr(A, "_try_html", lambda s, u, t, p: "short")   # both html attempts < gate
    monkeypatch.setattr(A, "_try_pdf", lambda s, u, t, p: "P" * 8000)
    text, src = A.fulltext_with_source("2606.26027", config=None)
    assert src == "pdf" and len(text) == 8000


def test_chain_all_fail_returns_empty(monkeypatch):
    monkeypatch.setattr(A, "_try_html", lambda *a: "")
    monkeypatch.setattr(A, "_try_pdf", lambda *a: "tiny")            # below MIN_FULLTEXT
    text, src = A.fulltext_with_source("2606.26027", config=None)
    assert text == "" and src == ""


def test_truncates_to_max_chars(monkeypatch):
    monkeypatch.setattr(A, "_try_html", lambda s, u, t, p: "Y" * 50000)
    text, _ = A.fulltext_with_source("2606.26027", config=None, max_chars=30000)
    assert len(text) == 30000


def test_version_stripped_in_fetch_urls(monkeypatch):
    seen = []
    monkeypatch.setattr(A, "_try_html", lambda s, u, t, p: seen.append(u) or "")
    monkeypatch.setattr(A, "_try_pdf", lambda s, u, t, p: seen.append(u) or "")
    A.fulltext_with_source("2606.27243v1", config=None)
    assert all("v1" not in u for u in seen) and any("/2606.27243" in u for u in seen)


# ---- routing inside fetch_article_text ----
def test_fetch_article_routes_arxiv_to_fulltext(monkeypatch):
    monkeypatch.setattr(A, "fetch_arxiv_fulltext", lambda aid, cfg, **kw: "FULL PAPER BODY")
    out = ART.fetch_article_text("https://arxiv.org/abs/2606.26027", config=None)
    assert out == "FULL PAPER BODY"


def test_fetch_article_falls_through_to_abstract(monkeypatch):
    monkeypatch.setattr(A, "fetch_arxiv_fulltext", lambda *a, **k: "")   # no full text

    class _Resp:
        headers = {"content-type": "text/html"}
        text = "<p>just the abstract</p>"

        def raise_for_status(self):
            pass

    class _Session:
        trust_env = False

        def get(self, *a, **k):
            return _Resp()
    monkeypatch.setattr(ART.requests, "Session", _Session)
    out = ART.fetch_article_text("https://arxiv.org/abs/2606.26027", config=None)
    assert "just the abstract" in out                                   # graceful degrade
