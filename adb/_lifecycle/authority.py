from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock
from time import monotonic
from typing import Generic, TypeAlias, TypeVar

from adb._lifecycle.result import (
    AcquireBlocked,
    AcquireBusy,
    AcquireExisting,
    AcquireStarted,
    AcquireStartResult,
    AcquireToken,
    CleanupRegistrationError,
    ReleaseAcquisitionRevoked,
    ReleaseGenerationMismatch,
    ReleaseInactive,
    ReleaseOwnershipDetached,
    ReleaseResult,
)


GenerationT = TypeVar("GenerationT")
OwnershipT = TypeVar("OwnershipT")


@dataclass(frozen=True, slots=True)
class _Idle(Generic[GenerationT]):
    """Current generation has no acquisition work or usable ownership."""

    generation: GenerationT


@dataclass(frozen=True, slots=True)
class _Acquiring(Generic[GenerationT]):
    """Current generation has one in-flight acquisition with commit authority."""

    generation: GenerationT
    token: AcquireToken
    cancellation: Event
    started_at: float = field(default_factory=monotonic, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class _Owned(Generic[GenerationT, OwnershipT]):
    """Current generation owns one usable resource."""

    generation: GenerationT
    ownership: OwnershipT


@dataclass(frozen=True, slots=True)
class _Draining(Generic[GenerationT]):
    """A revoked acquisition is draining after authority advanced to a new generation.

    ``generation`` is the current generation exposed by the authority. ``revoked_generation`` is
    the generation of the in-flight attempt that has already lost commit authority but has not yet
    returned to the authority.
    """

    generation: GenerationT
    revoked_generation: GenerationT
    token: AcquireToken
    cancellation: Event
    started_at: float = field(repr=False, compare=False)


_State: TypeAlias = (
    _Idle[GenerationT]
    | _Acquiring[GenerationT]
    | _Owned[GenerationT, OwnershipT]
    | _Draining[GenerationT]
)


@dataclass(frozen=True, slots=True)
class PendingSnapshot(Generic[GenerationT]):
    generation: GenerationT
    cancelled: bool
    age_seconds: float


@dataclass(frozen=True, slots=True)
class AuthoritySnapshot(Generic[GenerationT, OwnershipT]):
    """Atomic lifecycle authority snapshot for domain-facing state projection."""

    generation: GenerationT
    ownership: OwnershipT | None
    pending: PendingSnapshot[GenerationT] | None = None
    cleanup_registration_errors: tuple[str, ...] = ()


@dataclass(slots=True)
class _FailedCleanupRegistration:
    callback: Callable[[], None]
    diagnostic: str


class LifecycleAuthority(Generic[GenerationT, OwnershipT]):
    """Shared lifecycle authority state machine.

    Authority is represented as one explicit private state: ``_Idle``, ``_Acquiring``, ``_Owned``,
    or ``_Draining``. This makes in-flight acquisition and usable ownership mutually exclusive by
    construction. An ``AcquireToken`` is identity-only; generation fencing is expressed by state
    transitions, especially ``_Acquiring(G1) -> _Draining(G2, revoked_generation=G1)``.

    Domain lifecycles retain responsibility for validating acquisition constraints, obtaining and
    cleaning physical resources, constructing domain acquisition values, and publishing domain
    notifications. This authority owns only the concurrency-sensitive authority state and invokes
    narrow callbacks while holding its state lock when cleanup-debt registration must stay atomic
    with that state transition.

    All callbacks (including the issuer) must be short, non-reentrant, and perform no I/O, thread
    startup, or waits for external work. Nested state locks must follow a consistent lock order.
    Cleanup-registration callbacks must retain the resource in their closure and be idempotent: a
    failed registration is retained and retried before another acquisition can begin. Physical
    cleanup and notifications belong outside this authority's lock.

    The issuer must never reuse a generation within this authority scope. The adjacent-value check
    is a defensive check, not a replacement for that contract (it cannot detect ABA reuse).
    """

    def __init__(self, issue_generation: Callable[[], GenerationT]) -> None:
        if not callable(issue_generation):
            raise TypeError("issue_generation must be callable")
        self._issue_generation = issue_generation
        self._lock = Lock()
        self._state: _State[GenerationT, OwnershipT] = _Idle(issue_generation())
        self._failed_cleanup_registrations: list[_FailedCleanupRegistration] = []

    def _register_cleanup_locked(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except BaseException as exc:
            # Retain the closure/resource and block acquisition if registration failed before the
            # domain could record its cleanup debt. Clearing in-flight state alone would be unsafe.
            self._failed_cleanup_registrations.append(
                _FailedCleanupRegistration(callback, str(exc))
            )
            raise

    def _retry_cleanup_registrations_locked(self) -> bool:
        while self._failed_cleanup_registrations:
            registration = self._failed_cleanup_registrations[0]
            try:
                registration.callback()
            except Exception as exc:
                registration.diagnostic = str(exc)
                return False
            self._failed_cleanup_registrations.pop(0)
        return True

    @staticmethod
    def _pending_snapshot(
        state: _Acquiring[GenerationT] | _Draining[GenerationT],
    ) -> PendingSnapshot[GenerationT]:
        generation = (
            state.revoked_generation if isinstance(state, _Draining) else state.generation
        )
        return PendingSnapshot(
            generation=generation,
            cancelled=isinstance(state, _Draining) or state.cancellation.is_set(),
            age_seconds=max(0.0, monotonic() - state.started_at),
        )

    def snapshot(self) -> AuthoritySnapshot[GenerationT, OwnershipT]:
        with self._lock:
            state = self._state
            ownership = state.ownership if isinstance(state, _Owned) else None
            pending = (
                self._pending_snapshot(state)
                if isinstance(state, (_Acquiring, _Draining))
                else None
            )
            return AuthoritySnapshot(
                generation=state.generation,
                ownership=ownership,
                pending=pending,
                cleanup_registration_errors=tuple(
                    item.diagnostic for item in self._failed_cleanup_registrations
                ),
            )

    def begin_acquire(
        self,
        *,
        is_blocked: Callable[[], bool] | None = None,
    ) -> AcquireStartResult[GenerationT, OwnershipT]:
        """Atomically inspect authority and, when allowed, start one acquisition attempt."""

        if is_blocked is not None and not callable(is_blocked):
            raise TypeError("is_blocked must be callable or None")

        with self._lock:
            state = self._state
            if isinstance(state, _Owned):
                return AcquireExisting(state.ownership)
            if isinstance(state, _Acquiring):
                return AcquireBusy(draining=False)
            if isinstance(state, _Draining):
                return AcquireBusy(draining=True)
            if not isinstance(state, _Idle):
                raise RuntimeError("unsupported lifecycle authority state")

            if not self._retry_cleanup_registrations_locked():
                return AcquireBlocked(
                    "lifecycle cleanup registration failed; retry required"
                )
            if is_blocked is not None and is_blocked():
                return AcquireBlocked()

            token = AcquireToken()
            cancellation = Event()
            acquiring = _Acquiring(
                generation=state.generation,
                token=token,
                cancellation=cancellation,
            )
            self._state = acquiring
            return AcquireStarted(
                token=token,
                generation=acquiring.generation,
                cancellation=cancellation,
            )

    def abandon_acquire(
        self,
        token: AcquireToken,
        *,
        before_clear: Callable[[], None] | None = None,
    ) -> bool:
        """Finish a failed/abandoned acquisition and report whether it was already revoked.

        A failing ``before_clear`` still retires the matching in-flight state. Its
        resource-retaining cleanup registration remains queued for retry and blocks new acquisitions
        until registration
        succeeds.
        """

        if not isinstance(token, AcquireToken):
            raise TypeError("token must be AcquireToken")
        if before_clear is not None and not callable(before_clear):
            raise TypeError("before_clear must be callable or None")

        with self._lock:
            state = self._state
            matches_acquiring = isinstance(state, _Acquiring) and state.token is token
            matches_draining = isinstance(state, _Draining) and state.token is token
            if not matches_acquiring and not matches_draining:
                raise RuntimeError("acquisition token is not current")

            revoked = matches_draining
            try:
                if before_clear is not None:
                    self._register_cleanup_locked(before_clear)
            finally:
                self._state = _Idle(state.generation)
            return revoked

    def commit_acquire(
        self,
        token: AcquireToken,
        ownership: OwnershipT,
        *,
        on_superseded: Callable[[], None] | None = None,
    ) -> bool:
        """Commit ownership iff ``token`` still has authority; otherwise register cleanup."""

        if not isinstance(token, AcquireToken):
            raise TypeError("token must be AcquireToken")
        if ownership is None:
            raise ValueError("ownership cannot be None")
        if on_superseded is not None and not callable(on_superseded):
            raise TypeError("on_superseded must be callable or None")

        with self._lock:
            state = self._state
            if isinstance(state, _Acquiring) and state.token is token:
                self._state = _Owned(state.generation, ownership)
                return True

            try:
                if on_superseded is not None:
                    self._register_cleanup_locked(on_superseded)
            finally:
                if isinstance(state, _Draining) and state.token is token:
                    self._state = _Idle(state.generation)
            return False

    def release(
        self,
        expected: GenerationT,
        *,
        on_owned_release: Callable[[OwnershipT], None] | None = None,
        inconsistent_state_error: str = "lifecycle authority state is inconsistent",
    ) -> ReleaseResult[GenerationT, OwnershipT]:
        """Release matching authority, advance generation, and fence stale acquisition work."""

        if on_owned_release is not None and not callable(on_owned_release):
            raise TypeError("on_owned_release must be callable or None")
        if not isinstance(inconsistent_state_error, str):
            raise TypeError("inconsistent_state_error must be a string")
        normalized_error = inconsistent_state_error.strip()
        if not normalized_error:
            raise ValueError("inconsistent_state_error cannot be empty")

        with self._lock:
            state = self._state
            if expected != state.generation:
                return ReleaseGenerationMismatch(
                    current_generation=state.generation,
                    ownership=state.ownership if isinstance(state, _Owned) else None,
                )

            if isinstance(state, (_Idle, _Draining)):
                return ReleaseInactive(expected)

            released_generation = state.generation
            next_generation = self._issue_generation()
            if next_generation == released_generation:
                raise RuntimeError("issue_generation must return a fresh generation")

            if isinstance(state, _Acquiring):
                # Logical revocation is immediate, but the old attempt remains represented until
                # obtain returns. New acquisition is therefore blocked without comparing
                # generations.
                state.cancellation.set()
                self._state = _Draining(
                    generation=next_generation,
                    revoked_generation=released_generation,
                    token=state.token,
                    cancellation=state.cancellation,
                    started_at=state.started_at,
                )
                return ReleaseAcquisitionRevoked(released_generation)

            if not isinstance(state, _Owned):
                raise RuntimeError(normalized_error)

            ownership = state.ownership
            self._state = _Idle(next_generation)
            outcome = ReleaseOwnershipDetached(released_generation, ownership)
            if on_owned_release is not None:
                try:
                    self._register_cleanup_locked(lambda: on_owned_release(ownership))
                except Exception as exc:
                    raise CleanupRegistrationError(outcome) from exc
            return outcome


__all__ = [
    "AuthoritySnapshot",
    "LifecycleAuthority",
    "PendingSnapshot",
]
