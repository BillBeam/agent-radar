"""同一天多版本（core/versioning）——归档不清史、同 id 序幂等、墓碑共存。全离线。

登记簿不变量：末位条目必须是真实（非 lost）版本 = 无后缀当前文件；墓碑只出现在中间
（它记录「投递过但数据不可得」的一版，如 07-08 迁移日旧机器的第二跑）。"""
from __future__ import annotations

import json

import radar.core.versioning as V


def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(V.Paths, "digests", tmp_path, raising=True)
    return tmp_path


def _write_items(root, date, ids):
    (root / f"{date}.items.json").write_text(
        json.dumps([{"id": i, "title": f"T-{i}"} for i in ids]), encoding="utf-8")


def _write_md(root, date, text):
    d = root / date[:4] / date[5:7]
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date}.md").write_text(text, encoding="utf-8")


def test_first_write_registers_v1_no_archive(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    v = V.archive_if_new("2026-07-08", ["a", "b"], run_id="r1")
    assert v == 1
    assert [e["v"] for e in V.load_versions("2026-07-08")] == [1]
    assert not list(root.glob("*.v1.*"))          # nothing to archive on first write


def test_same_id_order_is_same_version(tmp_path, monkeypatch):
    """regen_v5 / rebuild / 同内容重投 = 原地更新，不产生版本噪声；历史日期（无登记簿）保持无登记簿。"""
    root = _root(tmp_path, monkeypatch)
    _write_items(root, "2026-07-01", ["a", "b"])  # 版本化之前就存在的老日期
    v = V.archive_if_new("2026-07-01", ["a", "b"], run_id="r2")
    assert v == 1
    assert V.load_versions("2026-07-01") == []    # legacy 日期不被动升级
    assert not (root / "2026-07-01.v1.items.json").exists()


def test_changed_ids_archive_previous_and_bump(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    _write_items(root, "2026-07-08", ["a", "b"])
    _write_md(root, "2026-07-08", "# old md\n")
    v = V.archive_if_new("2026-07-08", ["a", "c"], run_id="r3")
    assert v == 2
    # 旧版两件套改名存档，当前位空出（新 items.json 由 synthesize 随后写入）
    assert json.loads((root / "2026-07-08.v1.items.json").read_text())[0]["id"] == "a"
    assert not (root / "2026-07-08.items.json").exists()
    assert V.archived_md("2026-07-08", 1) == "# old md\n"
    assert not (root / "2026" / "07" / "2026-07-08.md").exists()
    reg = V.load_versions("2026-07-08")
    assert [e["v"] for e in reg] == [1, 2]
    assert reg[0].get("note")                     # v1 是自动补登的
    assert V.current_version("2026-07-08") == 2


def test_tombstone_in_middle_counts_toward_next_version(tmp_path, monkeypatch):
    """墓碑（lost）占据版本号但无文件；下一次真变化从 max(v)+1 继续。"""
    root = _root(tmp_path, monkeypatch)
    _write_items(root, "2026-07-08", ["x"])
    _write_md(root, "2026-07-08", "# v3 之前的当前版\n")
    (root / "2026-07-08.versions.json").write_text(json.dumps([
        {"v": 1, "ts": "t", "n_items": 9},
        {"v": 2, "ts": "t", "n_items": 10, "lost": True, "note": "数据未随迁移包"},
        {"v": 3, "ts": "t", "n_items": 1},
    ]), encoding="utf-8")
    v = V.archive_if_new("2026-07-08", ["y", "z"], run_id="r4")
    assert v == 4
    assert (root / "2026-07-08.v3.items.json").exists()      # 当前版按其真实版本号 3 存档
    reg = V.load_versions("2026-07-08")
    assert [e["v"] for e in reg] == [1, 2, 3, 4]
    assert reg[1]["lost"] is True                             # 墓碑原样保留
