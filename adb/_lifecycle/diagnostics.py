from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from adb._lifecycle.state_machine import PendingSnapshot


GenerationT = TypeVar("GenerationT")
AccessT = TypeVar("AccessT")


@dataclass(frozen=True, slots=True)
class LifecycleDiagnostics(Generic[GenerationT, AccessT]):
    """Point-in-time lifecycle diagnostics without resource-pool cleanup state."""

    generation: GenerationT
    pending: PendingSnapshot[GenerationT, AccessT] | None


__all__ = ["LifecycleDiagnostics"]
