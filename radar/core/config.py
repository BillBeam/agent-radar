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
    eval = DATA_DIR / "eval"                    # offline eval reports (P1 尺子)
    deepread_sources = DATA_DIR / "deepread_sources"   # exact grounding text deepread fed the LLM (for faithfulness eval)
    critic = DATA_DIR / "critic"                # per-item critic verdicts (signal-density sidecar)
    web = DATA_DIR / "web"                      # generated reading pages (gitignored; deployed to CF Pages)
    sources_yaml = CONFIG_DIR / "sources.yaml"
    taxonomy_yaml = CONFIG_DIR / "taxonomy.yaml"
    blocklist_yaml = CONFIG_DIR / "blocklist.yaml"
    seen_json = DATA_DIR / "state" / "seen.json"
    first_seen_json = DATA_DIR / "state" / "first_seen.json"
    deepread_ckpt = DATA_DIR / "state" / "deepread"    # per-item deepread checkpoint dir (crash-resume)
    memory_db = DATA_DIR / "memory.db"
    user_md = ROOT / "USER.md"          # personal profile (gitignored; only USER.example.md is committed)


class DingtalkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webhook: str
    secret: Optional[str] = None       # for 加签 (HMAC-SHA256) security mode


class DingtalkCardConfig(BaseModel):
    """Enterprise-internal-app robot delivering INTERACTIVE cards (👍/👎 → Stream callback).
    Secrets (client_id/client_secret) are NEVER stored here — env only. The non-secret ids
    may live in config.toml OR env (env wins). An empty [channels.dingtalk_card] section is
    enough to enable the channel and pull everything from env."""
    model_config = ConfigDict(extra="forbid")
    card_template_id: Optional[str] = None   # built once (API-create preferred) — the 命门
    user_id: Optional[str] = None            # 1v1 recipient staffId (or captured from a bot message)
    robot_code: Optional[str] = None         # the robot's RobotCode

    def resolved(self) -> dict:
        """Merge env over config; client_id/client_secret come ONLY from env."""
        return {
            "client_id": os.getenv("DINGTALK_CLIENT_ID"),
            "client_secret": os.getenv("DINGTALK_CLIENT_SECRET"),
            "card_template_id": os.getenv("DINGTALK_CARD_TEMPLATE_ID") or self.card_template_id,
            "user_id": os.getenv("DINGTALK_USER_ID") or self.user_id,
            "robot_code": os.getenv("DINGTALK_ROBOT_CODE") or self.robot_code,
        }

    def missing(self, keys: tuple[str, ...]) -> list[str]:
        """Which of `keys` are still unset after env+config resolution (for friendly errors)."""
        r = self.resolved()
        return [k for k in keys if not r.get(k)]


class WebReaderConfig(BaseModel):
    """Full 4-axis 详解 → a static reading page on Cloudflare Pages; the voting card links to it.
    The per-day URL is unguessable: seg = HMAC-SHA256(AGENT_RADAR_WEB_SECRET, date). Secrets
    (AGENT_RADAR_WEB_SECRET, CLOUDFLARE_API_TOKEN) live in ENV ONLY — never stored here, never
    logged. project_name/base_url are non-secret and may sit in config.toml OR env (env wins).
    An empty [channels.web_reader] section + the env vars is enough to enable it."""
    model_config = ConfigDict(extra="forbid")
    project_name: Optional[str] = None    # Cloudflare Pages project → https://{project_name}.pages.dev
    base_url: Optional[str] = None        # override (e.g. a custom domain); else derived from project_name

    def resolved(self) -> dict:
        """Non-secret config only (env over config). The two secrets are read straight from env at
        use-site so they never land in this dict (which could otherwise be logged)."""
        project = os.getenv("CLOUDFLARE_PAGES_PROJECT") or self.project_name
        base = (os.getenv("AGENT_RADAR_WEB_BASE_URL") or self.base_url
                or (f"https://{project}.pages.dev" if project else None))
        return {"project_name": project, "base_url": base.rstrip("/") if base else None,
                "account_id": os.getenv("CLOUDFLARE_ACCOUNT_ID")}

    def missing(self) -> list[str]:
        """Which required ids/creds are unset (NAMES only — never values)."""
        r = self.resolved()
        need = [] if r["project_name"] else ["project_name"]
        need += [k for k in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "AGENT_RADAR_WEB_SECRET")
                 if not os.getenv(k)]
        return need


class ChannelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local: bool = True                 # always-on local md archive
    macos: bool = True                 # desktop notification
    dingtalk: Optional[DingtalkConfig] = None   # enabled iff webhook provided (markdown push)
    dingtalk_card: Optional[DingtalkCardConfig] = None   # interactive 👍/👎 cards (Phase A)
    dingtalk_file: bool = True   # full 详解 → docx file to the same 1v1 (reuses card creds)
    web_reader: Optional[WebReaderConfig] = None   # full 详解 → CF Pages reading page; card links to #item-N


class ModelsConfig(BaseModel):
    """Model tiering to control subscription quota."""
    model_config = ConfigDict(extra="forbid")
    triage: str = "haiku"              # cheap, high-volume scoring
    deepread: str = "sonnet"           # grounded 详解
    synthesize: str = "sonnet"
    judge: str = "sonnet"              # offline eval judge (faithfulness / ranking); quality > cost
    critic: str = "sonnet"             # 批判层「有真料吗」判断；分寸需好判断，≤10 条一次调用很便宜


class MemoryConfig(BaseModel):
    """P2 content memory + personalization. Local SQLite (FTS5), no vectors, no extra API cost."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True                  # build the MemoryStore + run the `remember` stage
    personalize_rerank: bool = True       # inject USER.md 已会清单 into rerank (the A/B switch)
    recent_days: int = 30                 # window for the "近 N 天同主题已推过" down-weight signal


class RadarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Asia/Shanghai"
    daily_max_items: int = 10
    weekly_max_items: int = 25
    freshness_hours: float = 48.0          # daily: dedup makes a generous window safe
    weekly_freshness_hours: float = 192.0
    relevance_threshold: float = 6.0
    triage_pool_cap: int = 200         # safety ceiling on candidates sent to triage (recency-trimmed if exceeded)
    finalist_pool: int = 24            # how many survivors go to the rerank stage
    max_per_source: int = 3            # diversity: max items from one source in the final selection
    max_undated_per_source: int = 8    # bounded history: cap dateless (back-catalog) items per source
    deepread_top_k: int = 6

    token_budget_per_run: int = 200_000
    # --- proxy (first-class; many sources are Western and need a proxy from CN) ---
    http_proxy: Optional[str] = None   # explicit override; if set, wins and env proxies are ignored
    use_env_proxy: bool = True         # else honor HTTPS_PROXY/HTTP_PROXY/ALL_PROXY from the environment

    models: ModelsConfig = Field(default_factory=ModelsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    def window_hours(self, mode: str) -> float:
        return self.weekly_freshness_hours if mode == "weekly" else self.freshness_hours

    def max_items(self, mode: str) -> int:
        return self.weekly_max_items if mode == "weekly" else self.daily_max_items

    def proxy_settings(self) -> "tuple[Optional[dict], bool]":
        """Return (proxies_dict_or_None, trust_env) for a requests call.
        Explicit config proxy wins (and disables env). Otherwise honor the
        ambient HTTPS_PROXY/HTTP_PROXY/ALL_PROXY env vars (the user's real setup),
        unless use_env_proxy is turned off (then force direct)."""
        if self.http_proxy:
            return {"http": self.http_proxy, "https": self.http_proxy}, False
        if self.use_env_proxy:
            return None, True
        return None, False

    def resolved_proxy(self) -> Optional[str]:
        return self.http_proxy


def load_config(path: Optional[Path] = None) -> RadarConfig:
    """Load config.toml (if present) over defaults. Raises on invalid config."""
    path = path or (ROOT / "config.toml")
    data: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    return RadarConfig.model_validate(data)
