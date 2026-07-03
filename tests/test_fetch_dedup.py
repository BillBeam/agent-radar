"""fetch dedup — the SAME arXiv paper across sources / version suffixes collapses to one key.
Regression for 2026-07-03 [3]/[4] (AgenticSTS listed twice: arxiv `…2255v1` + hf `…2255`)."""
from __future__ import annotations

from types import SimpleNamespace

from radar.stages.fetch import _dedup_key


def _it(url: str, id: str = "x"):
    return SimpleNamespace(url=url, id=id)


def test_dedup_key_collapses_arxiv_versions_and_sources():
    k1 = _dedup_key(_it("https://arxiv.org/abs/2607.02255v1", "a"))        # arxiv-agents source
    k2 = _dedup_key(_it("https://arxiv.org/abs/2607.02255", "b"))          # hf-daily-papers (arxiv url, no ver)
    k3 = _dedup_key(_it("https://huggingface.co/papers/2607.02255", "c"))  # hf papers url form
    assert k1 == k2 == k3 == "arxiv:2607.02255"                           # same paper → same key


def test_dedup_key_keeps_distinct_arxiv_papers_apart():
    a = _dedup_key(_it("https://arxiv.org/abs/2607.02255v1", "a"))
    b = _dedup_key(_it("https://arxiv.org/abs/2607.09999", "b"))
    assert a != b                                                         # different papers stay distinct


def test_dedup_key_non_arxiv_keeps_per_url_id():
    it = _it("https://www.anthropic.com/engineering/containing-claude", id="blog-id-123")
    assert _dedup_key(it) == "blog-id-123"                               # non-arXiv unchanged (per-URL id)
