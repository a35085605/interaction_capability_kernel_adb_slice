from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from adb._lifecycle.state_machine import PendingSnapshot


GenerationT = TypeVar("GenerationT")


@dataclass(frozen=True, slots=True)
class LifecycleDiagnostics(Generic[GenerationT]):
    """Diagnostic samples of lifecycle state and cleanup, taken under their respective locks.

    These samples do not form a transaction across both components and are not acquire permission.
    """

    generation: GenerationT
    pending: PendingSnapshot[GenerationT] | None
    cleanup_registration_errors: tuple[str, ...]
    cleanup_pending_count: int
    cleanup_handoff_accepted_count: int
    cleanup_handoff_errors: tuple[str, ...]


__all__ = ["LifecycleDiagnostics"]
