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


# ---- V5 constructs: tables / fences / mermaid / section heads / backtop / read-stats ----
V5_SAMPLE = """# Agent Radar · 2026-07-05
## 🆕 今日新增
### [1] [Paper A](https://arxiv.org/abs/1)
**🎯 一句话核心洞察**
洞察正文。
**🔧 核心机制完整拆解**
**机制一：采样器**
机制正文，长度凑一点，这样时长估计不为零。

```mermaid
flowchart LR
  A["输入"] --> B["输出"]
```

图：这张图看数据流向。

**🧪 实验与证据（完整）**
| 方法 | 得分 | 提升 |
|---|---|---|
| 基线 | 61.2 | - |
| 本文 | 74.5 | +13.3 |

表里的每个数字都被解释。

```python
print("hi <b>")
```

### [2] [Paper B](https://arxiv.org/abs/2)
第二篇正文。
"""


def test_table_renders_scrollable_with_zebra_semantics():
    h = render_day_page(V5_SAMPLE, date="2026-07-05")
    assert '<div class="tbl"><table>' in h
    assert "<th>方法</th><th>得分</th><th>提升</th>" in h
    assert "<td>74.5</td>" in h and "<td>+13.3</td>" in h
    assert "|---|" not in h                                   # separator row consumed


def test_malformed_table_row_padded_to_header():
    h = render_day_page("| a | b |\n|---|---|\n| only |\n", date="d")
    assert h.count("<td>") == 2 and "<td>only</td>" in h      # short row padded, page intact


def test_fence_code_block_escaped():
    h = render_day_page(V5_SAMPLE, date="2026-07-05")
    assert '<pre class="code"><code>print(&quot;hi &lt;b&gt;&quot;)</code></pre>' in h


def test_mermaid_without_renderer_degrades_to_code():
    h = render_day_page(V5_SAMPLE, date="2026-07-05")         # no mermaid_svg injected
    assert 'flowchart LR' in h and '<figure class="diagram">' not in h


def test_mermaid_renderer_injected_and_failure_isolated():
    ok = render_day_page(V5_SAMPLE, date="d", mermaid_svg=lambda code: "<svg id='x'>ok</svg>")
    assert '<figure class="diagram"><svg id=\'x\'>ok</svg></figure>' in ok

    def boom(code):
        raise RuntimeError("bad diagram")
    degraded = render_day_page(V5_SAMPLE, date="d", mermaid_svg=boom)
    assert '<figure class="diagram">' not in degraded         # exception → code block, page fine
    assert "flowchart LR" in degraded and "<h1>" in degraded


def test_section_heads_promoted_subheads_not():
    h = render_day_page(V5_SAMPLE, date="2026-07-05")
    assert '<p class="axis sect">🎯 一句话核心洞察</p>' in h
    assert '<p class="axis sect">🧪 实验与证据（完整）</p>' in h
    assert '<p class="axis">机制一：采样器</p>' in h            # sub-head stays plain axis


def test_caption_paragraph_styled():
    h = render_day_page(V5_SAMPLE, date="2026-07-05")
    assert '<p class="caption">图：这张图看数据流向。</p>' in h


def test_backtop_after_each_item_and_toc_read_stats():
    h = render_day_page(V5_SAMPLE, date="2026-07-05")
    assert h.count("↑ 返回目录") == 2                          # one per item
    assert '<nav class="toc" id="toc"' in h                   # backtop target exists
    assert 'class="mins"' in h and "分钟" in h                 # per-item read-time estimate


def test_real_mmdc_renders_chinese_flowchart(tmp_path, monkeypatch):
    """Integration smoke: the actual chosen path (mmdc → SVG). Skips if npx unavailable."""
    import shutil as _sh

    import pytest
    from radar.core.config import Paths
    monkeypatch.setattr(Paths, "web", tmp_path / "web")       # isolate the svg cache
    if not _sh.which("npx"):
        pytest.skip("npx not on PATH")
    from radar.channels._mermaid import mermaid_to_svg
    svg = mermaid_to_svg('flowchart LR\n  A["输入"] --> B["判断"]')
    if svg is None:
        pytest.skip("mmdc unavailable in this environment (degrade path covered elsewhere)")
    assert svg.startswith("<svg") and "输入" in svg
    assert 'id="mmd-' in svg                                  # per-diagram scoped id
    svg2 = mermaid_to_svg('flowchart LR\n  A["输入"] --> B["判断"]')
    assert svg2 == svg                                        # content-hash cache hit
