"""Identity-leak scanner — one 口径 for anything that leaves the machine or enters git.

Two pattern layers:
  1. LOCAL terms — data/self_improve/leak_terms.local.txt (gitignored): the real identity
     vocabulary (employer / real name / knows-him-only context words), hand-distilled from
     the local profile docs. One entry per line; `#` = comment; wrap in /slashes/ for a
     regex, anything else is a case-insensitive literal. The vocabulary itself IS identity
     data → the file must never be committed; when it's missing this module warns LOUDLY
     instead of silently passing.
  2. BUILTIN generic categories (safe to commit — contain no real identity): job-hunting
     context words that have no business in radar's committed/pushed artifacts.

Consumers: scripts/leak_scan.py (CLI, pre-commit self-audit) and self_improve/review.py
(the DingTalk summary self-check — private channel, but the same red line applies).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ..core.config import Paths

TERMS_FILE = Paths.data / "self_improve" / "leak_terms.local.txt"

# Generic job-hunting context — radar is a personal tech-intel product; these words inside
# its artifacts almost always mean profile context leaked into an output.
# (adjacent-literal concat so this file never contains its own trigger words → self-scan clean)
_BUILTIN = ["简" "历", "面" "试", "求" "职", "跳" "槽", "猎" "头", "入" "职"]


def load_patterns(terms_file: Optional[Path] = None) -> tuple[list[tuple[str, re.Pattern]], Optional[str]]:
    """(patterns, warning) — (label, compiled-regex) pairs. `warning` is a loud note when
    the local vocabulary is unavailable/partial (builtins still apply — never silent)."""
    pats: list[tuple[str, re.Pattern]] = [
        (f"builtin:{t}", re.compile(re.escape(t), re.IGNORECASE)) for t in _BUILTIN]
    path = terms_file or TERMS_FILE
    warning: Optional[str] = None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return pats, (f"⚠ 本地身份词表缺失（{path}）——本次只跑了内置通用类，"
                      "扫描不完整；补上词表前别把「通过」当真。")
    except Exception as e:  # noqa: BLE001
        return pats, f"⚠ 本地身份词表读取失败（{e!r}）——本次只跑了内置通用类。"
    for raw in lines:
        t = raw.strip()
        if not t or t.startswith("#"):
            continue
        if len(t) > 2 and t.startswith("/") and t.endswith("/"):
            try:
                pats.append(("local:regex", re.compile(t[1:-1], re.IGNORECASE)))
            except re.error:
                warning = f"⚠ 词表里有非法正则被跳过：{t}"
        else:
            pats.append((f"local:{t}", re.compile(re.escape(t), re.IGNORECASE)))
    return pats, warning


def scan_text(text: str, *, source: str = "<text>",
              terms_file: Optional[Path] = None) -> tuple[list[dict], Optional[str]]:
    """Scan one text. Returns (hits, load_warning); hits=[] means clean *under the loaded
    vocabulary* — a non-None warning means the vocabulary was incomplete."""
    pats, warning = load_patterns(terms_file)
    hits: list[dict] = []
    for ln, line in enumerate((text or "").splitlines(), 1):
        for label, pat in pats:
            if pat.search(line):
                hits.append({"source": source, "line": ln, "label": label,
                             "excerpt": line.strip()[:120]})
    return hits, warning


def scan_files(paths: list[Path],
               terms_file: Optional[Path] = None) -> tuple[list[dict], Optional[str]]:
    """Scan files; unreadable files are reported as hits (fail-closed, never skipped)."""
    pats, warning = load_patterns(terms_file)
    hits: list[dict] = []
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            hits.append({"source": str(p), "line": 0, "label": "unreadable",
                         "excerpt": repr(e)[:120]})
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            for label, pat in pats:
                if pat.search(line):
                    hits.append({"source": str(p), "line": ln, "label": label,
                                 "excerpt": line.strip()[:120]})
    return hits, warning
