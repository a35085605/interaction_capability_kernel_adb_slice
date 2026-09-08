from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock
from typing import Generic, TypeVar


GenerationT = TypeVar("GenerationT")
OwnershipT = TypeVar("OwnershipT")


@dataclass(frozen=True, slots=True)
class LifecyclePendingAcquire(Generic[GenerationT]):
    """One in-flight acquisition fenced by the generation captured at its start."""

    generation: GenerationT
    cancellation: Event


@dataclass(frozen=True, slots=True)
class LifecycleAcquireOwned(Generic[OwnershipT]):
    """An acquisition cannot start because usable ownership already exists."""

    ownership: OwnershipT


@dataclass(frozen=True, slots=True)
class LifecycleAcquireBusy:
    """An acquisition cannot start because another acquisition is still in flight."""


@dataclass(frozen=True, slots=True)
class LifecycleAcquireBlocked:
    """An acquisition cannot start because a domain precondition currently blocks it."""


@dataclass(frozen=True, slots=True)
class LifecycleAuthoritySnapshot(Generic[GenerationT, OwnershipT]):
    """Atomic lifecycle authority snapshot for domain-facing state projection."""

    generation: GenerationT
    ownership: OwnershipT | None


@dataclass(frozen=True, slots=True)
class LifecycleReleaseGenerationMismatch(Generic[GenerationT, OwnershipT]):
    current_generation: GenerationT
    ownership: OwnershipT | None


@dataclass(frozen=True, slots=True)
class LifecycleReleaseInactive(Generic[GenerationT]):
    generation: GenerationT


@dataclass(frozen=True, slots=True)
class LifecycleReleasePending(Generic[GenerationT]):
    generation: GenerationT


@dataclass(frozen=True, slots=True)
class LifecycleReleaseOwned(Generic[GenerationT, OwnershipT]):
    generation: GenerationT
    ownership: OwnershipT


class LifecycleAuthorityCore(Generic[GenerationT, OwnershipT]):
    """Shared generation, pending-acquire, ownership, and release state machine.

    Domain lifecycles retain responsibility for validating acquisition constraints, obtaining and
    cleaning physical resources, constructing domain acquisition values, and publishing domain
    notifications. This core owns only the concurrency-sensitive authority state and invokes narrow
    callbacks while holding its state lock when a domain effect must stay atomic with that state.
    """

    def __init__(self, issue_generation: Callable[[], GenerationT]) -> None:
        if not callable(issue_generation):
            raise TypeError("issue_generation must be callable")
        self._issue_generation = issue_generation
        self._lock = Lock()
        self._generation = issue_generation()
        self._pending: LifecyclePendingAcquire[GenerationT] | None = None
        self._ownership: OwnershipT | None = None

    def snapshot(self) -> LifecycleAuthoritySnapshot[GenerationT, OwnershipT]:
        with self._lock:
            return LifecycleAuthoritySnapshot(self._generation, self._ownership)

    def run_if_no_pending(self, action: Callable[[], None]) -> bool:
        """Run ``action`` atomically only while no acquisition is in flight."""

        if not callable(action):
            raise TypeError("action must be callable")
        with self._lock:
            if self._pending is not None:
                return False
            action()
            return True

    def begin_acquire(
        self,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> (
        LifecycleAcquireOwned[OwnershipT]
        | LifecycleAcquireBusy
        | LifecycleAcquireBlocked
        | LifecyclePendingAcquire[GenerationT]
    ):
        """Atomically inspect authority and, when allowed, install one pending acquisition."""

        if is_blocked is not None and not callable(is_blocked):
            raise TypeError("is_blocked must be callable or None")

        with self._lock:
            ownership = self._ownership
            if ownership is not None:
                return LifecycleAcquireOwned(ownership)
            if self._pending is not None:
                return LifecycleAcquireBusy()
            if is_blocked is not None and is_blocked():
                return LifecycleAcquireBlocked()

            pending = LifecyclePendingAcquire(
                generation=self._generation,
                cancellation=Event(),
            )
            self._pending = pending
            return pending

    def abandon_acquire(
        self,
        pending: LifecyclePendingAcquire[GenerationT],
        *,
        before_clear: Callable[[], None] | None = None,
    ) -> bool:
        """Clear a completed/failed pending acquisition and report whether its generation
        revoked.
        """

        if not isinstance(pending, LifecyclePendingAcquire):
            raise TypeError("pending must be LifecyclePendingAcquire")
        if before_clear is not None and not callable(before_clear):
            raise TypeError("before_clear must be callable or None")

        with self._lock:
            revoked = self._generation != pending.generation
            if before_clear is not None:
                before_clear()
            if self._pending is pending:
                self._pending = None
            return revoked

    def commit_acquire(
        self,
        pending: LifecyclePendingAcquire[GenerationT],
        ownership: OwnershipT,
        *,
        on_commit: Callable[[], None] | None = None,
        on_superseded: Callable[[], None] | None = None,
    ) -> bool:
        """Commit ownership iff ``pending`` is current; otherwise retire the obtained
        resource.
        """

        if not isinstance(pending, LifecyclePendingAcquire):
            raise TypeError("pending must be LifecyclePendingAcquire")
        if on_commit is not None and not callable(on_commit):
            raise TypeError("on_commit must be callable or None")
        if on_superseded is not None and not callable(on_superseded):
            raise TypeError("on_superseded must be callable or None")

        with self._lock:
            if self._pending is pending and self._generation == pending.generation:
                self._pending = None
                self._ownership = ownership
                if on_commit is not None:
                    on_commit()
                return True

            if on_superseded is not None:
                on_superseded()
            if self._pending is pending:
                self._pending = None
            return False

    def release(
        self,
        expected: GenerationT,
        *,
        on_owned_release: Callable[[OwnershipT], None] | None = None,
        inconsistent_state_error: str = "lifecycle authority state is inconsistent",
    ) -> (
        LifecycleReleaseGenerationMismatch[GenerationT, OwnershipT]
        | LifecycleReleaseInactive[GenerationT]
        | LifecycleReleasePending[GenerationT]
        | LifecycleReleaseOwned[GenerationT, OwnershipT]
    ):
        """Release matching authority, advance generation, and fence stale acquisition work."""

        if on_owned_release is not None and not callable(on_owned_release):
            raise TypeError("on_owned_release must be callable or None")
        if not isinstance(inconsistent_state_error, str):
            raise TypeError("inconsistent_state_error must be a string")
        normalized_error = inconsistent_state_error.strip()
        if not normalized_error:
            raise ValueError("inconsistent_state_error cannot be empty")

        with self._lock:
            if expected != self._generation:
                return LifecycleReleaseGenerationMismatch(
                    current_generation=self._generation,
                    ownership=self._ownership,
                )

            pending = self._pending
            current_pending = (
                pending
                if pending is not None and pending.generation == self._generation
                else None
            )
            ownership = self._ownership
            if current_pending is None and ownership is None:
                return LifecycleReleaseInactive(expected)

            released_generation = self._generation
            self._generation = self._issue_generation()

            if current_pending is not None:
                # Deliberately retain the pending object until the in-flight obtain returns. A new
                # acquire therefore remains blocked even though generation has already advanced.
                current_pending.cancellation.set()
                return LifecycleReleasePending(released_generation)

            if ownership is None:
                raise RuntimeError(normalized_error)

            self._ownership = None
            if on_owned_release is not None:
                on_owned_release(ownership)
            return LifecycleReleaseOwned(released_generation, ownership)


__all__ = [
    "LifecycleAcquireBlocked",
    "LifecycleAcquireBusy",
    "LifecycleAcquireOwned",
    "LifecycleAuthorityCore",
    "LifecycleAuthoritySnapshot",
    "LifecyclePendingAcquire",
    "LifecycleReleaseGenerationMismatch",
    "LifecycleReleaseInactive",
    "LifecycleReleaseOwned",
    "LifecycleReleasePending",
]
