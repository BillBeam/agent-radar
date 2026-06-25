"""Lenient JSON extraction from LLM output (handles ```json fences / prose)."""
from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """Best-effort parse of a JSON object/array embedded in model output.
    Raises ValueError if nothing parseable is found."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty text")
    # 1) whole thing
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2) fenced block
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 3) first balanced {...} or [...]
    for open_c, close_c in (("[", "]"), ("{", "}")):
        start = text.find(open_c)
        end = text.rfind(close_c)
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in model output")
