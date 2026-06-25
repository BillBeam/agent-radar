"""Configuration: pydantic-validated, fail-fast.

Defaults live here; the user's real secrets/overrides live in config.toml
(gitignored). Bad config raises on startup rather than mid-run.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# agent-radar/ project root (this file is radar/core/config.py)
ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
PROMPTS_DIR = ROOT / "prompts"


class Paths:
    root = ROOT
    config = CONFIG_DIR
    data = DATA_DIR
    prompts = PROMPTS_DIR
    candidates = DATA_DIR / "candidates"
    digests = DATA_DIR / "digests"
    trace = DATA_DIR / "trace"
    metrics = DATA_DIR / "metrics"
    state = DATA_DIR / "state"
    feedback = DATA_DIR / "feedback"
    sources_yaml = CONFIG_DIR / "sources.yaml"
    taxonomy_yaml = CONFIG_DIR / "taxonomy.yaml"
    blocklist_yaml = CONFIG_DIR / "blocklist.yaml"
    seen_json = DATA_DIR / "state" / "seen.json"
    memory_db = DATA_DIR / "memory.db"


class DingtalkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webhook: str
    secret: Optional[str] = None       # for 加签 (HMAC-SHA256) security mode


class ChannelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local: bool = True                 # always-on local md archive
    macos: bool = True                 # desktop notification
    dingtalk: Optional[DingtalkConfig] = None   # enabled iff webhook provided


class ModelsConfig(BaseModel):
    """Model tiering to control subscription quota."""
    model_config = ConfigDict(extra="forbid")
    triage: str = "haiku"              # cheap, high-volume scoring
    deepread: str = "sonnet"           # grounded 详解
    synthesize: str = "sonnet"


class RadarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Shanghai"
    daily_max_items: int = 10
    weekly_max_items: int = 25
    freshness_hours: float = 48.0          # daily: dedup makes a generous window safe
    weekly_freshness_hours: float = 192.0
    relevance_threshold: float = 6.0
    triage_pool_cap: int = 80          # cap candidates sent to the triage LLM (cost control)
    deepread_top_k: int = 6
    token_budget_per_run: int = 200_000
    http_proxy: Optional[str] = None

    models: ModelsConfig = Field(default_factory=ModelsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)

    def window_hours(self, mode: str) -> float:
        return self.weekly_freshness_hours if mode == "weekly" else self.freshness_hours

    def max_items(self, mode: str) -> int:
        return self.weekly_max_items if mode == "weekly" else self.daily_max_items

    def resolved_proxy(self) -> Optional[str]:
        # Only an explicitly-configured proxy — never the ambient HTTP_PROXY env
        # (which in this setup is a corp proxy unreachable from the user's Mac).
        return self.http_proxy


def load_config(path: Optional[Path] = None) -> RadarConfig:
    """Load config.toml (if present) over defaults. Raises on invalid config."""
    path = path or (ROOT / "config.toml")
    data: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    return RadarConfig.model_validate(data)
