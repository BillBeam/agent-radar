"""Abstract ports — the seams of the hexagon.

Every varying capability (a source type, a delivery channel, a quality rule, a
pipeline stage, the LLM backend) implements one of these and
self-registers. Core code depends only on these interfaces, never on a concrete
adapter — so adding a capability is adding a file, never editing the core.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional

from .models import Digest, Item, RunContext, Source, TimeWindow


class SourceAdapter(ABC):
    """Fetches recent items from one source. One adapter per SourceType."""

    @abstractmethod
    def fetch(self, source: Source, window: TimeWindow) -> list[Item]:
        ...


class QualityRule(ABC):
    """A composable filter/transform in the quality gate. Order matters."""

    name: str = "rule"

    @abstractmethod
    def apply(self, items: list[Item], ctx: RunContext) -> list[Item]:
        ...


class Stage(ABC):
    """One step of the pipeline. Mutates ctx in place.

    `critical=True` means a failure aborts the run; otherwise the pipeline logs
    the error, records degradation, and continues (graceful degradation).
    """

    name: str = "stage"
    critical: bool = False

    @abstractmethod
    def run(self, ctx: RunContext) -> None:
        ...


class Channel(ABC):
    """A delivery target (local file, macOS notification, DingTalk, ...)."""

    name: str = "channel"

    def is_enabled(self, config: Any) -> bool:
        return True

    @abstractmethod
    def send(self, digest: Digest, ctx: RunContext) -> bool:
        ...


@dataclass
class LLMResult:
    text: str
    raw: Any = None
    usage: Optional[dict[str, Any]] = None
    model: Optional[str] = None
    ok: bool = True
    error: Optional[str] = None


class LLMClient(ABC):
    """The reasoning backend. Default adapter shells out to `claude -p`
    (subscription); a future adapter can hit the Anthropic API."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> LLMResult:
        ...
