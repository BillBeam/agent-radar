"""md → HTML reading-page renderer — structure fidelity + #item-N anchors + TOC (pure, no network)."""
from __future__ import annotations

from radar.channels._web_render import render_day_page

SAMPLE = """# Agent Radar · 2026-06-30
> 扫描 28 源 · 候选 117 · 今日新增 3
## 🎯 今日 TL;DR
一句话。
## 🆕 今日新增
### [1] [Some Paper Title](https://arxiv.org/abs/2606.1)
*arXiv (agent/LLM)*　paper · eval
> ⚠️ 可跳过 · 与条目[2]重复
**① 核心机制**
这是**核心**内容，含 *斜体* 与 [内链](https://x.org/a)。
- 第一点
- 第二点
**takeaway**
一句话总结。
### [2] [Second](https://arxiv.org/abs/2606.2)
*arXiv*　paper
正文 n < 10 的比较。
"""


def _h() -> str:
    return render_day_page(SAMPLE, date="2026-06-30")


def test_noindex_and_head():
    h = _h()
    assert '<meta name="robots" content="noindex, nofollow">' in h   # privacy = B
    assert "<!doctype html>" in h and 'lang="zh-CN"' in h
    assert "<title>Agent Radar · 2026-06-30</title>" in h


def test_heading_hierarchy():
    h = _h()
    assert "<h1>Agent Radar · 2026-06-30</h1>" in h                  # # → h1
    assert "<h2>🎯 今日 TL;DR</h2>" in h                              # ## → h2
    assert "<blockquote>扫描 28 源 · 候选 117 · 今日新增 3</blockquote>" in h   # > meta


def test_item_anchor_and_title_link():
    h = _h()
    assert '<h3 id="item-1" class="item">' in h                      # ### [1] → anchored h3
    assert '<h3 id="item-2" class="item">' in h
    assert 'href="https://arxiv.org/abs/2606.1"' in h                # title link preserved
    assert "[1] " in h                                               # the [N] prefix stays visible


def test_toc_present_and_links_to_anchors():
    h = _h()
    assert 'class="toc"' in h
    assert 'href="#item-1"' in h and 'href="#item-2"' in h
    assert "Some Paper Title" in h and "Second" in h                 # TOC shows plain "[N] title"


def test_axis_subheads_not_literal_bold():
    h = _h()
    assert '<p class="axis">① 核心机制</p>' in h                      # **①…** → styled axis subhead
    assert '<p class="axis">takeaway</p>' in h
    assert "**" not in h                                             # no leftover literal ** markers


def test_inline_bold_italic_link_and_bullets():
    h = _h()
    assert "<strong>核心</strong>" in h
    assert "<em>斜体</em>" in h
    assert '<a href="https://x.org/a" target="_blank" rel="noopener">内链</a>' in h
    assert "<ul><li>第一点</li><li>第二点</li></ul>" in h              # consecutive - → one <ul>


def test_critic_quote_and_html_escape():
    h = _h()
    assert "<blockquote>⚠️ 可跳过 · 与条目[2]重复</blockquote>" in h
    assert "n &lt; 10" in h                                          # raw < escaped — never a broken tag


def test_hr_and_blank_skipped():
    h = render_day_page("# H\n\n---\n正文", date="d")
    assert "<h1>H</h1>" in h and "<p>正文</p>" in h and "---" not in h
