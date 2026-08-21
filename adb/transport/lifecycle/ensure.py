from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import math
from numbers import Integral, Real
from time import monotonic, sleep
from typing import Protocol, runtime_checkable

from adb.errors import AdbError
from adb.server.model import AdbServerEndpoint
from adb.server.ownership import AdbOwnedServer
from adb.transport.configuration import AdbConfiguredTransport
from adb.transport.inventory.model import (
    AdbConnectionState,
    AdbConnectionType,
    AdbDevicesSnapshot,
    AdbTrackedDevice,
)
from adb.transport.inventory.reader import AdbDevicesSnapshotReader
from adb.transport.inventory.resolution import (
    AdbConfiguredTransportResolutionStatus,
    resolve_configured_transport,
)
from adb.transport.lifecycle.establishment import (
    AdbTransportEstablisher,
    AdbTransportEstablishmentAttempt,
)
from adb.transport.selection import AdbDeviceSerial
from eventing import EventPublisher
from native_attempt import NativeAttemptResult, NativeAttemptStatus


def _normalize_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return normalized


def _normalize_state(value: object, *, field_name: str) -> AdbConnectionState | int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{field_name} values must be integers")
    raw = int(value)
    try:
        return AdbConnectionState(raw)
    except ValueError:
        return raw


def _normalize_states(
    value: object,
    *,
    field_name: str,
    allow_empty: bool,
) -> frozenset[AdbConnectionState | int]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    normalized = frozenset(
        _normalize_state(item, field_name=field_name)
        for item in value
    )
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def _normalize_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbTransportEnsurePolicy:
    """Polling policy for one bounded transport-readiness ensure operation.

    States not listed as acceptable or blocked remain waiting states. This preserves future
    open-enum values without silently treating them as ready or permanently failed.
    """

    timeout_seconds: float
    acceptable_states: frozenset[AdbConnectionState | int]
    blocked_states: frozenset[AdbConnectionState | int] = frozenset()
    probe_interval_seconds: float = 0.5

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timeout_seconds",
            _normalize_positive_seconds(
                self.timeout_seconds,
                field_name="ADB transport ensure timeout",
            ),
        )
        acceptable = _normalize_states(
            self.acceptable_states,
            field_name="acceptable_states",
            allow_empty=False,
        )
        blocked = _normalize_states(
            self.blocked_states,
            field_name="blocked_states",
            allow_empty=True,
        )
        if acceptable & blocked:
            raise ValueError("acceptable_states and blocked_states must be disjoint")
        object.__setattr__(self, "acceptable_states", acceptable)
        object.__setattr__(self, "blocked_states", blocked)
        object.__setattr__(
            self,
            "probe_interval_seconds",
            _normalize_positive_seconds(
                self.probe_interval_seconds,
                field_name="ADB transport ensure probe interval",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbTransportEnsureReadiness:
    """Request bounded readiness verification against one owned server generation."""

    server: AdbOwnedServer
    configuration: AdbConfiguredTransport
    policy: AdbTransportEnsurePolicy

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbOwnedServer):
            raise TypeError("server must be AdbOwnedServer")
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.policy, AdbTransportEnsurePolicy):
            raise TypeError("policy must be AdbTransportEnsurePolicy")

    @property
    def endpoint(self) -> AdbServerEndpoint:
        return self.server.endpoint

    @property
    def serial(self) -> AdbDeviceSerial:
        return self.configuration.serial


class AdbTransportEnsureStatus(str, Enum):
    """Terminal status of one transport-readiness ensure operation."""

    SATISFIED = "satisfied"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"
    AMBIGUOUS = "ambiguous"
    TYPE_MISMATCH = "type_mismatch"


class AdbTransportReadinessSatisfaction(str, Enum):
    """How the final readiness condition became satisfied."""

    ALREADY_SATISFIED = "already_satisfied"
    ACHIEVED = "achieved"


class AdbTransportPresenceSatisfaction(str, Enum):
    """When polling first found the configured binding during one episode."""

    ALREADY_PRESENT = "already_present"
    OBSERVED = "observed"


@dataclass(frozen=True, slots=True)
class AdbTransportEnsureResult:
    """Terminal polling evidence without collapsing command success into readiness."""

    operation: AdbTransportEnsureReadiness
    status: AdbTransportEnsureStatus
    satisfaction: AdbTransportReadinessSatisfaction | None
    presence_satisfaction: AdbTransportPresenceSatisfaction | None
    attempts: tuple[NativeAttemptResult, ...]
    final_snapshot: AdbDevicesSnapshot | None = None
    final_row: AdbTrackedDevice | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbTransportEnsureReadiness):
            raise TypeError("operation must be AdbTransportEnsureReadiness")
        if not isinstance(self.status, AdbTransportEnsureStatus):
            raise TypeError("status must be AdbTransportEnsureStatus")
        if self.satisfaction is not None and not isinstance(
            self.satisfaction, AdbTransportReadinessSatisfaction
        ):
            raise TypeError("satisfaction must be AdbTransportReadinessSatisfaction or None")
        if self.presence_satisfaction is not None and not isinstance(
            self.presence_satisfaction, AdbTransportPresenceSatisfaction
        ):
            raise TypeError(
                "presence_satisfaction must be AdbTransportPresenceSatisfaction or None"
            )
        if not isinstance(self.attempts, tuple) or not all(
            isinstance(attempt, NativeAttemptResult) for attempt in self.attempts
        ):
            raise TypeError("attempts must be a tuple of NativeAttemptResult values")
        if self.final_snapshot is not None and not isinstance(
            self.final_snapshot, AdbDevicesSnapshot
        ):
            raise TypeError("final_snapshot must be AdbDevicesSnapshot or None")
        if self.final_row is not None and not isinstance(self.final_row, AdbTrackedDevice):
            raise TypeError("final_row must be AdbTrackedDevice or None")
        if self.final_row is not None:
            if self.final_snapshot is None or self.final_row not in self.final_snapshot.devices:
                raise ValueError("final_row must belong to final_snapshot")
            if self.final_row.serial != self.operation.serial.value:
                raise ValueError("final_row serial must match ensure operation")
            if self.final_row.connection_type not in (
                self.operation.configuration.expected_connection_type,
                AdbConnectionType.UNKNOWN,
            ):
                raise ValueError("final_row connection type must match configured transport")
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB transport ensure diagnostic",
            ),
        )

        if self.status is AdbTransportEnsureStatus.SATISFIED:
            if self.satisfaction is None or self.final_row is None:
                raise ValueError("satisfied ensure requires satisfaction and final_row")
            if self.final_row.state not in self.operation.policy.acceptable_states:
                raise ValueError("satisfied ensure requires an acceptable final state")
        elif self.satisfaction is not None:
            raise ValueError("unsatisfied ensure cannot carry satisfaction")


@runtime_checkable
class AdbTransportEnsurer(Protocol):
    """Ensure bounded readiness and declare supported establishment routes.

    Ensuring owns fresh snapshot probing and bounded verification, not long-lived transport
    tracking. Implementations used by transport supervision must support concurrent
    ``ensure`` calls for different configured transports.
    """

    def supports_establishment(
        self,
        configuration: AdbConfiguredTransport,
    ) -> bool:
        """Return whether absence can trigger an active establishment attempt."""
        ...

    def ensure(
        self,
        operation: AdbTransportEnsureReadiness,
    ) -> AdbTransportEnsureResult:
        ...


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]


@dataclass(slots=True)
class _ReadinessEpisodeState:
    """Pure mutable state and snapshot decisions for one readiness ensure operation."""

    operation: AdbTransportEnsureReadiness
    attempts: list[NativeAttemptResult] = field(default_factory=list)
    presence: AdbTransportPresenceSatisfaction | None = None
    satisfaction: AdbTransportReadinessSatisfaction | None = None
    final_snapshot: AdbDevicesSnapshot | None = None
    final_row: AdbTrackedDevice | None = None
    diagnostic: str | None = None
    probes_attempted: int = 0
    establishment_attempted: bool = False
    latest_resolution_status: AdbConfiguredTransportResolutionStatus | None = None

    @property
    def policy(self) -> AdbTransportEnsurePolicy:
        return self.operation.policy

    def record_probe_failure(self, error: AdbError) -> None:
        self.probes_attempted += 1
        self.latest_resolution_status = None
        self.diagnostic = str(error) or error.__class__.__name__

    def evaluate_snapshot(
        self,
        snapshot: AdbDevicesSnapshot,
    ) -> AdbTransportEnsureStatus | None:
        initial = self.probes_attempted == 0
        self.probes_attempted += 1
        self.final_snapshot = snapshot

        resolution = resolve_configured_transport(
            self.operation.configuration,
            snapshot,
        )
        self.latest_resolution_status = resolution.status

        if resolution.status is AdbConfiguredTransportResolutionStatus.AMBIGUOUS:
            self.final_row = None
            return AdbTransportEnsureStatus.AMBIGUOUS
        if resolution.status is AdbConfiguredTransportResolutionStatus.TYPE_MISMATCH:
            self.final_row = None
            return AdbTransportEnsureStatus.TYPE_MISMATCH
        if resolution.status is AdbConfiguredTransportResolutionStatus.ABSENT:
            self.final_row = None
            return None

        row = resolution.row
        assert row is not None
        self.final_row = row
        if self.presence is None:
            self.presence = (
                AdbTransportPresenceSatisfaction.ALREADY_PRESENT
                if initial
                else AdbTransportPresenceSatisfaction.OBSERVED
            )

        if row.state in self.policy.acceptable_states:
            self.satisfaction = (
                AdbTransportReadinessSatisfaction.ALREADY_SATISFIED
                if initial
                else AdbTransportReadinessSatisfaction.ACHIEVED
            )
            return AdbTransportEnsureStatus.SATISFIED
        if row.state in self.policy.blocked_states:
            return AdbTransportEnsureStatus.BLOCKED
        return None

    @property
    def should_attempt_establishment(self) -> bool:
        return (
            self.latest_resolution_status
            is AdbConfiguredTransportResolutionStatus.ABSENT
            and self.presence is None
            and not self.establishment_attempted
        )

    def record_establishment(self, attempt: NativeAttemptResult) -> None:
        self.establishment_attempted = True
        self.attempts.append(attempt)

    def deadline_status(self) -> AdbTransportEnsureStatus:
        if self.attempts and self.attempts[-1].status is NativeAttemptStatus.FAILED:
            return AdbTransportEnsureStatus.FAILED
        return AdbTransportEnsureStatus.TIMED_OUT

    def result(
        self,
        status: AdbTransportEnsureStatus,
    ) -> AdbTransportEnsureResult:
        return AdbTransportEnsureResult(
            operation=self.operation,
            status=status,
            satisfaction=(
                self.satisfaction
                if status is AdbTransportEnsureStatus.SATISFIED
                else None
            ),
            presence_satisfaction=self.presence,
            attempts=tuple(self.attempts),
            final_snapshot=self.final_snapshot,
            final_row=self.final_row,
            diagnostic=self.diagnostic,
        )


class AdbTransportEnsureOrchestrator:
    """Ensure one configured transport is ready within one deadline.

    Long-lived inventory tracking, generation fencing, and recovery triggering belong to
    ``AdbConfiguredTransportSupervisor``. This orchestrator independently re-probes current
    inventory before acting, performs at most one supported establishment command, and polls
    fresh snapshots until readiness reaches a terminal state or the deadline expires.
    """

    def __init__(
        self,
        server: AdbOwnedServer,
        snapshot_reader: AdbDevicesSnapshotReader,
        publisher: EventPublisher,
        *,
        establisher: AdbTransportEstablisher | None = None,
        _monotonic: _MonotonicClock = monotonic,
        _sleep: _Sleeper = sleep,
    ) -> None:
        if not isinstance(server, AdbOwnedServer):
            raise TypeError("server must be AdbOwnedServer")
        if not callable(getattr(snapshot_reader, "read", None)):
            raise TypeError("snapshot_reader must provide read()")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        if establisher is not None and not isinstance(
            establisher,
            AdbTransportEstablisher,
        ):
            raise TypeError(
                "establisher must satisfy AdbTransportEstablisher or be None"
            )
        self.server = server
        self.endpoint = server.endpoint
        self._snapshot_reader = snapshot_reader
        self._publisher = publisher
        self._establisher = establisher
        self._monotonic = _monotonic
        self._sleep = _sleep

    def supports_establishment(
        self,
        configuration: AdbConfiguredTransport,
    ) -> bool:
        if not isinstance(configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        establisher = self._establisher
        if establisher is None:
            return False
        supported = establisher.supports(configuration)
        if not isinstance(supported, bool):
            raise TypeError("establisher supports() must return bool")
        return supported

    def ensure(
        self,
        operation: AdbTransportEnsureReadiness,
    ) -> AdbTransportEnsureResult:
        if not isinstance(operation, AdbTransportEnsureReadiness):
            raise TypeError("operation must be AdbTransportEnsureReadiness")
        if operation.server is not self.server:
            raise ValueError("operation server does not match ensure orchestrator owned server")

        policy = operation.policy
        deadline = self._monotonic() + policy.timeout_seconds
        episode = _ReadinessEpisodeState(operation)

        while True:
            try:
                snapshot = self._snapshot_reader.read(self.server)
            except AdbError as exc:
                episode.record_probe_failure(exc)
            else:
                terminal = episode.evaluate_snapshot(snapshot)
                if terminal is not None:
                    return self._complete(episode, terminal)

                if (
                    episode.should_attempt_establishment
                    and self.supports_establishment(operation.configuration)
                ):
                    episode.record_establishment(
                        self._establish(operation.configuration)
                    )
                    # Match server ensure semantics: verify once immediately after the command,
                    # even when the command consumed the remaining deadline.
                    continue

            remaining = deadline - self._monotonic()
            if remaining <= 0.0:
                return self._complete(episode, episode.deadline_status())
            self._sleep(min(policy.probe_interval_seconds, remaining))

    def _establish(
        self,
        configuration: AdbConfiguredTransport,
    ) -> NativeAttemptResult:
        from adb.transport.signal import AdbTransportCommandCompleted

        establisher = self._establisher
        if establisher is None or not establisher.supports(configuration):
            raise RuntimeError(
                "configured transport has no active establishment route"
            )
        attempt = establisher.establish(configuration)
        if not isinstance(attempt, AdbTransportEstablishmentAttempt):
            raise TypeError(
                "establisher must return AdbTransportEstablishmentAttempt"
            )
        self._publisher.publish(
            AdbTransportCommandCompleted(attempt.operation, attempt.result)
        )
        return attempt.result

    def _complete(
        self,
        episode: _ReadinessEpisodeState,
        status: AdbTransportEnsureStatus,
    ) -> AdbTransportEnsureResult:
        from adb.transport.signal import AdbTransportEnsureCompleted

        result = episode.result(status)
        self._publisher.publish(AdbTransportEnsureCompleted(result))
        return result


__all__ = [
    "AdbTransportEnsurePolicy",
    "AdbTransportEnsureReadiness",
    "AdbTransportEnsureResult",
    "AdbTransportEnsureOrchestrator",
    "AdbTransportEnsureStatus",
    "AdbTransportEnsurer",
    "AdbTransportPresenceSatisfaction",
    "AdbTransportReadinessSatisfaction",
]
