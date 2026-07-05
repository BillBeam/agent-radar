#!/usr/bin/env python3
"""Pre-commit identity-leak scan — CLI wrapper over radar.self_improve.leak_scan.

    .venv/bin/python scripts/leak_scan.py FILE [FILE...]

exit 0 = clean under the loaded vocabulary（词表缺失时会大声说明，别把这种「通过」当真）
exit 1 = hits（逐条列 file:line + 命中类别）
exit 2 = usage error

真实识别词表 = data/self_improve/leak_terms.local.txt（gitignored——词表本身就是身份数据）。
本脚本零身份内容，可提交。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root → import radar.*

from radar.self_improve.leak_scan import scan_files  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    hits, warning = scan_files([Path(a) for a in argv])
    if warning:
        print(warning)
    if not hits:
        print(f"leak_scan: {len(argv)} 个文件通过（0 命中）")
        return 0
    print(f"leak_scan: {len(hits)} 处命中：")
    for h in hits:
        print(f"  {h['source']}:{h['line']}  [{h['label']}]  {h['excerpt']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
