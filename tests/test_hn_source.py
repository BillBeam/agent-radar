"""HN 适配器 — points 闸移到客户端、recency 闸走 created_at_i（2026-07-09 Algolia 回归）。

那天 Algolia 把 `points` 从 numericAttributesForFiltering 摘掉，`numericFilters=points>N`
开始 400；适配器按设计吞掉了每个关键词的异常，于是**整源静默贡献 0 条**（07-08 还有 10-11 条）。
这里锁死修复后的契约：请求里不许再出现 points 过滤，低分条目必须被客户端丢掉。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import radar.sources.hackernews as HN
from radar.core.models import Source, TimeWindow


def _hit(title, points, age_h=1.0, oid="1"):
    when = datetime.now(timezone.utc) - timedelta(hours=age_h)
    return {"objectID": oid, "title": title, "points": points, "num_comments": 3,
            "url": f"https://example.com/{oid}", "created_at": when.isoformat()}


def _source():
    return Source(id="hackernews", name="HN", category="community", type="hackernews",
                  url=HN.API, weight=0.9,
                  params={"keywords": ["MCP"], "min_points": 60, "per_kw": 20})


class _Src(HN.HackerNewsSource):
    def __init__(self, hits):
        self.hits = hits
        self.urls: list[str] = []
        self.log = None

    def get_json(self, url):
        self.urls.append(url)
        return {"hits": self.hits}


def test_request_no_longer_filters_on_points_and_gates_recency_server_side():
    src = _Src([])
    src.fetch(_source(), TimeWindow(hours=48.0))
    q = parse_qs(urlparse(src.urls[0]).query)
    assert "points>" not in q["numericFilters"][0]          # the 400 that killed the source
    assert q["numericFilters"][0].startswith("created_at_i>")
    assert urlparse(src.urls[0]).path.endswith("/search")   # points-desc custom ranking


def test_low_point_stories_are_dropped_client_side():
    src = _Src([_hit("big", 272, oid="1"), _hit("edge", 60, oid="2"),
                _hit("small", 17, oid="3"), _hit("zero", 0, oid="4")])
    items = src.fetch(_source(), TimeWindow(hours=48.0))
    titles = {i.title for i in items}
    assert titles == {"big", "edge"}                        # min_points=60 is inclusive
    assert all("points" in i.summary for i in items)


def test_stale_stories_still_filtered_by_the_window():
    src = _Src([_hit("fresh", 100, age_h=1, oid="1"), _hit("stale", 900, age_h=200, oid="2")])
    items = src.fetch(_source(), TimeWindow(hours=48.0))
    assert [i.title for i in items] == ["fresh"]


def test_one_bad_keyword_never_kills_the_source():
    class _Flaky(_Src):
        def get_json(self, url):
            self.urls.append(url)
            if "bad" in url:
                raise RuntimeError("400 Client Error")
            return {"hits": [_hit("ok", 99)]}

    s = _source()
    s.params["keywords"] = ["bad", "MCP"]
    items = _Flaky([]).fetch(s, TimeWindow(hours=48.0))
    assert [i.title for i in items] == ["ok"]
