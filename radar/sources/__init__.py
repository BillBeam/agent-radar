"""Source registry loading + feed validation."""
from __future__ import annotations

import time
from typing import Optional

import yaml

from ..core import registry
from ..core.config import Paths, RadarConfig, load_config
from ..core.models import Source, TimeWindow


def load_sources(only_enabled: bool = True) -> list[Source]:
    raw = yaml.safe_load(Paths.sources_yaml.read_text(encoding="utf-8")) or {}
    entries = raw.get("sources", [])
    sources = [Source.model_validate(e) for e in entries]
    return [s for s in sources if s.enabled] if only_enabled else sources


def _adapter_for(source: Source, config: RadarConfig, log=None):
    cls = registry.get("source", source.type.value)
    return cls(config=config, log=log)


def validate_sources(config: Optional[RadarConfig] = None) -> int:
    """Hit every source with a wide window and report liveness. (I run this — no
    user action.) Returns 0 if at least 80% of enabled sources are live."""
    registry.load_adapters()
    config = config or load_config()
    sources = load_sources(only_enabled=False)
    # Wide window: validate tests endpoint liveness/parseability, not recency.
    # (A live feed with no posts in the daily window is fine — it'll contribute
    # when it next updates.)
    window = TimeWindow(24 * 3650)

    print(f"validating {len(sources)} sources from {Paths.sources_yaml.name}\n")
    ok = 0
    enabled = 0
    rows: list[tuple[str, str, str]] = []
    for s in sources:
        if not s.enabled:
            rows.append(("·", s.id, "disabled"))
            continue
        enabled += 1
        try:
            adapter = _adapter_for(s, config)
            t0 = time.monotonic()
            items = adapter.fetch(s, window)
            dt = (time.monotonic() - t0) * 1000
            if items:
                ok += 1
                rows.append(("✓", s.id, f"{len(items):3d} items · {dt:.0f}ms · [{s.category}]"))
            else:
                rows.append(("⚠", s.id, f"0 items · {dt:.0f}ms — check endpoint/window"))
        except Exception as e:  # noqa: BLE001
            rows.append(("✗", s.id, f"{type(e).__name__}: {str(e)[:80]}"))

    width = max((len(r[1]) for r in rows), default=10)
    for mark, sid, detail in rows:
        print(f"  {mark} {sid.ljust(width)}  {detail}")

    print(f"\n{ok}/{enabled} enabled sources live")
    return 0 if (enabled == 0 or ok / enabled >= 0.8) else 1
