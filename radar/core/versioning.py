"""同一天多版本（2026-07-08 迁移日引入）——重跑同一 date 不清史。

动机：迁移日两台机器先后投递同一天，第二次投递静默覆盖了第一版的 items.json/归档 md，
且钉钉对复用的 outTrackId 直接忽略新数据（用户看到的还是旧卡）。既然 deepread 按 item
checkpoint 已保证「重跑不重付 opus」，版本化保住的是**已付过 token 的产物本身**。

设计：
- 「版本」= items.json 的 **id 顺序**。同 id 序 = 同一版原地更新（regen_v5 重写详解、
  rebuild_site 重部署都不产生版本噪声）；id 集合/顺序变了才算新版本。
- 当前版永远是无后缀文件（{date}.items.json、YYYY/MM/{date}.md）——所有既有读者
  （mark / eval / 网页 / 卡片回退）零改动；旧版挪成 {date}.v{k}.* 后缀存档。
- 登记簿 {date}.versions.json = append-only 数组 [{v, ts, n_items, run_id?, note?, lost?}]，
  最大 v = 当前版。lost=True 是「墓碑」：那一版确实投递过但数据不可得（如迁移日第二跑
  只存在于旧机器），网页版本注记会如实列出而不给死链。
- 历史日期零迁移：登记簿不存在时视为 v1，首次变化时自动补登 v1 再升 v2。
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import Paths
from .io import atomic_write_json, read_json


def _reg_path(date: str) -> Path:
    return Paths.digests / f"{date}.versions.json"


def _md_path(date: str) -> Path:
    return Paths.digests / date[:4] / date[5:7] / f"{date}.md"


def load_versions(date: str) -> list[dict]:
    """登记簿条目（可能为空 = 该日期从未版本化，视作单版本）。"""
    reg = read_json(_reg_path(date), []) or []
    return reg if isinstance(reg, list) else []


def current_version(date: str) -> int:
    reg = load_versions(date)
    return max((int(e.get("v", 1)) for e in reg), default=1)


def _entry(v: int, n_items: Optional[int], run_id: Optional[str],
           note: Optional[str] = None, lost: bool = False) -> dict:
    e: dict = {"v": v, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if n_items is not None:
        e["n_items"] = n_items
    if run_id:
        e["run_id"] = run_id
    if note:
        e["note"] = note
    if lost:
        e["lost"] = True
    return e


def archive_if_new(date: str, new_ids: list[str], *, n_items: Optional[int] = None,
                   run_id: Optional[str] = None, log: Any = None) -> int:
    """synthesize 在写 {date}.items.json **之前**调用。返回本次写入所属的版本号。

    - 该日无 items.json → 首版：登记（沿用登记簿里已有的墓碑序号之后）并返回。
    - 现存 items.json 的 id 序 == new_ids → 同版原地更新，登记簿不动。
    - 不同 → 现存 items.json / 归档 md 挪成 .v{cur} 后缀，追加新登记，返回 cur+1。
    """
    items_p = Paths.digests / f"{date}.items.json"
    reg = load_versions(date)
    cur = max((int(e.get("v", 1)) for e in reg), default=0)

    existing = read_json(items_p, None) if items_p.exists() else None
    if existing is None:
        v = cur + 1 if cur else 1
        reg.append(_entry(v, n_items if n_items is not None else len(new_ids), run_id))
        atomic_write_json(_reg_path(date), reg)
        return v

    old_ids = [it.get("id") for it in existing if isinstance(it, dict)]
    if old_ids == list(new_ids):
        return cur or 1                      # 同一版：regen/重投不产生版本噪声

    # —— 真变化：归档现存版，登记新版 ——
    if cur == 0:                             # 历史日期首次版本化：补登现存版为 v1
        cur = 1
        reg.append(_entry(1, len(old_ids), None, note="首版（版本化前既有数据，自动补登）"))
    shutil.move(str(items_p), str(Paths.digests / f"{date}.v{cur}.items.json"))
    md_p = _md_path(date)
    if md_p.exists():
        shutil.move(str(md_p), str(md_p.with_name(f"{date}.v{cur}.md")))
    v = cur + 1
    reg.append(_entry(v, n_items if n_items is not None else len(new_ids), run_id))
    atomic_write_json(_reg_path(date), reg)
    if log:
        log.info("digest versioned — previous kept", date=date, prev=cur, now=v)
    return v


def archived_md(date: str, v: int) -> Optional[str]:
    """v 版的归档 md 全文（无后缀=当前版不在此列）；不存在 → None。"""
    p = _md_path(date).with_name(f"{date}.v{v}.md")
    return p.read_text(encoding="utf-8") if p.exists() else None


def archived_items(date: str, v: int) -> list[dict]:
    return read_json(Paths.digests / f"{date}.v{v}.items.json", []) or []
