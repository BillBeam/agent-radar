"""B3 重大发布豁免 — 回归锁（PART D fixtures）。

默认跑（无网络无 LLM）：锁 rubric 措辞——豁免、护栏、例句三件套缺一不可，防未来 prompt
改动静默退化（重大发布重新被压死 / 补丁被豁免误抬）。
可选真跑（RADAR_LLM_TESTS=1）：用 fixtures 三条真调 haiku，断言分数带（补丁≤4、重大≥8、
nightly≤2）。方差更全面的多轮重放在 scripts/prove_triage_exemption.py。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from radar.core.config import Paths

FIXTURES = Path(__file__).parent / "fixtures" / "timeliness_cases.json"


def _rubric() -> str:
    return Paths.prompts.joinpath("triage.md").read_text(encoding="utf-8")


def test_rubric_has_major_release_exemption():
    """豁免必须在：重大前沿发布即使细节薄也 8-10。"""
    r = _rubric()
    assert "Major frontier release exemption" in r
    assert "8–10 even if engineering detail is thin" in r


def test_rubric_exemption_has_guardrail():
    """护栏必须在（单向措辞）：例行补丁/nightly 照旧 0-4，不因核心厂商而抬分。"""
    r = _rubric()
    assert "补丁·小版本号递增（vX.Y.Z）" in r
    assert "照旧 0–4" in r
    assert "不因出自核心厂商而抬分" in r


def test_rubric_has_new_product_floor_with_guardrail():
    """新一方产品地板（Claude Tag 案）：命名新产品 ≥6-7 上桌；打包/可用性/定价照旧压；不抬到 8+。"""
    r = _rubric()
    assert "New first-party product floor" in r
    assert "at least 6–7 even if the blurb is thin marketing" in r
    assert "地区可用性" in r and "照旧 0–4" in r
    assert "8–10 仍只属于新模型代际/重大能力/协议变更" in r


def test_rubric_exemption_has_examples():
    """例句必须在：保 Opus 4.6 级 / 压 v2.1.201 补丁 tag 与 nightly。"""
    r = _rubric()
    assert "Claude Opus 4.6" in r and "8+" in r
    assert "v2.1.201" in r and "≤4" in r
    assert "nightly" in r and "≤2" in r


def test_fixture_cases_wellformed():
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert cases["patch_release"]["title"] == "v2.1.201"          # 用户点名的真实案例
    assert cases["patch_release"]["expected_max_score"] <= 4
    assert cases["major_release_counterfactual"]["expected_min_score"] >= 8
    assert "SYNTHETIC" in cases["major_release_counterfactual"]["id"]   # 构造物必须带标记
    assert cases["nightly_release"]["expected_max_score"] <= 2


@pytest.mark.skipif(not os.environ.get("RADAR_LLM_TESTS"),
                    reason="live haiku call — set RADAR_LLM_TESTS=1 to run")
def test_exemption_score_bands_live():
    """真调 haiku 一次验三条分数带（多轮方差版在 scripts/prove_triage_exemption.py）。"""
    from radar.core.config import load_config
    from radar.llm.claude_code import ClaudeCodeLLM

    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))
    keys = ["patch_release", "major_release_counterfactual", "nightly_release"]
    lines = [f"[{i}] ({cases[k]['category']}|{cases[k]['source_name']}) "
             f"{cases[k]['title']} :: {cases[k]['summary'][:160]}"
             for i, k in enumerate(keys)]
    user = ("TOPIC TAXONOMY (use exact strings): agent-harness\nSELF_COMPONENTS: none\n\n"
            f"Score these {len(lines)} candidates per the rubric. Return ONLY the JSON array.\n\n"
            + "\n".join(lines))
    llm = ClaudeCodeLLM(config=load_config())
    data, res = llm.complete_json(user, system=_rubric(), model="haiku", tag="test")
    assert isinstance(data, list), res.error
    by_i = {int(r["i"]): float(r["score"]) for r in data}
    assert by_i[0] <= cases["patch_release"]["expected_max_score"]
    assert by_i[1] >= cases["major_release_counterfactual"]["expected_min_score"]
    assert by_i[2] <= cases["nightly_release"]["expected_max_score"]
