"""arXiv SOURCE adapter — query building + the C2-tightened config filter (no network)."""
from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import unquote_plus

import yaml

from radar.core.config import Paths
from radar.sources.arxiv import ArxivSource


def _query(params: dict) -> str:
    """Decoded search_query the adapter would hit (ArxivSource._url only reads source.params)."""
    return unquote_plus(ArxivSource()._url(SimpleNamespace(params=params)))


def test_url_builds_cat_AND_keyword_filter():
    q = _query({"categories": ["cs.AI", "cs.MA"], "keywords": ["agent", "tool use"]})
    assert "cat:cs.AI" in q and "cat:cs.MA" in q
    assert 'abs:"agent"' in q and 'abs:"tool use"' in q
    assert ") AND (" in q                                    # (cats) AND (keywords)


def test_narrowed_config_drops_cs_lg_and_wide_keywords():
    """C2: sources.yaml 的 arxiv-agents 去掉 cs.LG + LLM/language model/reasoning，
    保 agent/agentic（[1] 那类 agent-safety 论文靠它存活）。"""
    data = yaml.safe_load(Paths.sources_yaml.read_text(encoding="utf-8"))
    arx = next(s for s in data["sources"] if s["id"] == "arxiv-agents")
    cats = arx["params"]["categories"]
    kws = [k.lower() for k in arx["params"]["keywords"]]
    assert "cs.LG" not in cats                               # 模型噪声主漏口已去
    assert {"cs.AI", "cs.CL", "cs.MA", "cs.SE"} <= set(cats)
    for wide in ("llm", "language model", "reasoning"):
        assert wide not in kws                               # 宽关键词已去
    assert "agent" in kws and "agentic" in kws               # [1] 存活兜底


def test_narrowed_config_query_excludes_noise():
    """端到端：用收紧后的真实 config 建 query → 不含 cs.LG / LLM / reasoning，含 agent + cs.AI。"""
    data = yaml.safe_load(Paths.sources_yaml.read_text(encoding="utf-8"))
    arx = next(s for s in data["sources"] if s["id"] == "arxiv-agents")
    q = _query(arx["params"])
    assert "cs.LG" not in q
    assert 'abs:"LLM"' not in q and 'abs:"reasoning"' not in q
    assert 'abs:"agent"' in q and "cat:cs.AI" in q
