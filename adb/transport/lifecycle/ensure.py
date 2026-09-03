from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import math
from numbers import Real
from time import monotonic, sleep
from typing import Protocol, runtime_checkable

from adb.errors import AdbError
from networking import TcpAddress
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServerIdentity
from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
)
from adb.transport.model import AdbTransport, AdbTransportState
from adb.transport_list.model import AdbTransportList
from adb.transport_list.reader import AdbTransportListReader
from adb.transport_list.interpretation import (
    AdbObservedTransportCompatibility,
    classify_observed_transport,
)
from adb.transport.resolution import (
    AdbConfiguredTransportResolutionStatus,
)
from adb.transport.lifecycle.control.port import AdbTcpConnect, AdbTcpConnector
from adb.transport.identity import AdbDeviceSerial
from eventing import EventPublisher
from native_attempt import NativeAttemptResult, NativeAttemptStatus


def _normalize_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return normalized


def _normalize_states(
    value: object,
    *,
    field_name: str,
    allow_empty: bool,
) -> frozenset[AdbTransportState]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if not all(isinstance(item, AdbTransportState) for item in value):
        raise TypeError(f"{field_name} values must be AdbTransportState")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} cannot be empty")
    return value


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
class AdbTcpTransportEnsurePolicy:
    """Configure one bounded transport-readiness polling episode with non-terminal states remaining
    pending.
    """

    timeout_seconds: float
    acceptable_states: frozenset[AdbTransportState]
    blocked_states: frozenset[AdbTransportState] = frozenset()
    probe_interval_seconds: float = 0.5

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timeout_seconds",
            _normalize_positive_seconds(
                self.timeout_seconds,
                field_name="ADB TCP transport ensure timeout",
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
                field_name="ADB TCP transport ensure probe interval",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbTcpTransportEnsureReadiness:
    """Request bounded readiness verification against one server lifetime."""

    server: AdbServerIdentity
    endpoint: AdbServerEndpoint
    configuration: AdbConfiguredTransport
    policy: AdbTcpTransportEnsurePolicy

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")
        if not isinstance(self.configuration.transport, AdbTcpTransportConfiguration):
            raise ValueError("TCP ensure requires an AdbTcpTransportConfiguration")
        if not isinstance(self.policy, AdbTcpTransportEnsurePolicy):
            raise TypeError("policy must be AdbTcpTransportEnsurePolicy")

    @property
    def serial(self) -> AdbDeviceSerial:
        return self.configuration.serial


class AdbTcpTransportEnsureStatus(str, Enum):
    """Terminal status of one transport-readiness ensure operation."""

    SATISFIED = "satisfied"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    BLOCKED = "blocked"
    AMBIGUOUS = "ambiguous"
    TYPE_MISMATCH = "type_mismatch"


class AdbTcpTransportReadinessSatisfaction(str, Enum):
    """How the final readiness condition became satisfied."""

    ALREADY_SATISFIED = "already_satisfied"
    ACHIEVED = "achieved"


class AdbTcpTransportPresenceSatisfaction(str, Enum):
    """When polling first found the configured binding during one episode."""

    ALREADY_PRESENT = "already_present"
    OBSERVED = "observed"


@dataclass(frozen=True, slots=True)
class AdbTcpTransportEnsureResult:
    """Terminal polling evidence that records command outcome and readiness independently."""

    operation: AdbTcpTransportEnsureReadiness
    status: AdbTcpTransportEnsureStatus
    satisfaction: AdbTcpTransportReadinessSatisfaction | None
    presence_satisfaction: AdbTcpTransportPresenceSatisfaction | None
    attempts: tuple[NativeAttemptResult, ...]
    final_transport_list: AdbTransportList | None = None
    final_transport: AdbTransport | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbTcpTransportEnsureReadiness):
            raise TypeError("operation must be AdbTcpTransportEnsureReadiness")
        if not isinstance(self.status, AdbTcpTransportEnsureStatus):
            raise TypeError("status must be AdbTcpTransportEnsureStatus")
        if self.satisfaction is not None and not isinstance(
            self.satisfaction, AdbTcpTransportReadinessSatisfaction
        ):
            raise TypeError("satisfaction must be AdbTcpTransportReadinessSatisfaction or None")
        if self.presence_satisfaction is not None and not isinstance(
            self.presence_satisfaction, AdbTcpTransportPresenceSatisfaction
        ):
            raise TypeError(
                "presence_satisfaction must be AdbTcpTransportPresenceSatisfaction or None"
            )
        if not isinstance(self.attempts, tuple) or not all(
            isinstance(attempt, NativeAttemptResult) for attempt in self.attempts
        ):
            raise TypeError("attempts must be a tuple of NativeAttemptResult values")
        if self.final_transport_list is not None and not isinstance(
            self.final_transport_list, AdbTransportList
        ):
            raise TypeError("final_transport_list must be AdbTransportList or None")
        if self.final_transport is not None and not isinstance(
            self.final_transport, AdbTransport
        ):
            raise TypeError("final_transport must be AdbTransport or None")
        if self.final_transport is not None:
            if (
                self.final_transport_list is None
                or self.final_transport not in self.final_transport_list.transports
            ):
                raise ValueError("final_transport must belong to final_transport_list")
            if not self.final_transport.matches_serial(self.operation.serial):
                raise ValueError("final_transport serial must match ensure operation")
            if (
                classify_observed_transport(self.operation.configuration, self.final_transport)
                is AdbObservedTransportCompatibility.MISMATCH
            ):
                raise ValueError("final_transport type must match configured transport")
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB TCP transport ensure diagnostic",
            ),
        )

        if self.status is AdbTcpTransportEnsureStatus.SATISFIED:
            if self.satisfaction is None or self.final_transport is None:
                raise ValueError("satisfied ensure requires satisfaction and final_transport")
            if (
                self.final_transport.state.transport_state
                not in self.operation.policy.acceptable_states
            ):
                raise ValueError("satisfied ensure requires an acceptable final state")
        elif self.satisfaction is not None:
            raise ValueError("unsatisfied ensure cannot carry satisfaction")


@runtime_checkable
class AdbTcpTransportEnsurer(Protocol):
    """Ensure bounded readiness for configured TCP transports with concurrent episodes for distinct
    transports.
    """

    def ensure(
        self,
        operation: AdbTcpTransportEnsureReadiness,
    ) -> AdbTcpTransportEnsureResult:
        """Ensure one configured TCP transport reaches a terminal readiness result."""
        ...


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]


@dataclass(slots=True)
class _ReadinessEpisodeState:
    """Mutable state for one readiness ensure operation."""

    operation: AdbTcpTransportEnsureReadiness
    attempts: list[NativeAttemptResult] = field(default_factory=list)
    presence: AdbTcpTransportPresenceSatisfaction | None = None
    satisfaction: AdbTcpTransportReadinessSatisfaction | None = None
    final_transport_list: AdbTransportList | None = None
    final_transport: AdbTransport | None = None
    diagnostic: str | None = None
    probes_attempted: int = 0
    connect_attempted: bool = False
    latest_resolution_status: AdbConfiguredTransportResolutionStatus | None = None

    @property
    def policy(self) -> AdbTcpTransportEnsurePolicy:
        return self.operation.policy

    def record_probe_failure(self, error: AdbError) -> None:
        self.probes_attempted += 1
        self.latest_resolution_status = None
        self.diagnostic = str(error) or error.__class__.__name__

    def evaluate_transport_list(
        self,
        transport_list: AdbTransportList,
    ) -> AdbTcpTransportEnsureStatus | None:
        initial = self.probes_attempted == 0
        self.probes_attempted += 1
        self.final_transport_list = transport_list

        resolution = transport_list.resolve_configured_transport(self.operation.configuration)
        self.latest_resolution_status = resolution.status

        if resolution.status is AdbConfiguredTransportResolutionStatus.AMBIGUOUS:
            self.final_transport = None
            return AdbTcpTransportEnsureStatus.AMBIGUOUS
        if resolution.status is AdbConfiguredTransportResolutionStatus.TYPE_MISMATCH:
            self.final_transport = None
            return AdbTcpTransportEnsureStatus.TYPE_MISMATCH
        if resolution.status is AdbConfiguredTransportResolutionStatus.ABSENT:
            self.final_transport = None
            return None

        transport = resolution.transport
        assert transport is not None
        self.final_transport = transport
        if self.presence is None:
            self.presence = (
                AdbTcpTransportPresenceSatisfaction.ALREADY_PRESENT
                if initial
                else AdbTcpTransportPresenceSatisfaction.OBSERVED
            )

        if transport.state.transport_state in self.policy.acceptable_states:
            self.satisfaction = (
                AdbTcpTransportReadinessSatisfaction.ALREADY_SATISFIED
                if initial
                else AdbTcpTransportReadinessSatisfaction.ACHIEVED
            )
            return AdbTcpTransportEnsureStatus.SATISFIED
        if transport.state.transport_state in self.policy.blocked_states:
            return AdbTcpTransportEnsureStatus.BLOCKED
        return None

    @property
    def should_attempt_connect(self) -> bool:
        return (
            self.latest_resolution_status
            is AdbConfiguredTransportResolutionStatus.ABSENT
            and self.presence is None
            and not self.connect_attempted
        )

    def record_connect(self, attempt: NativeAttemptResult) -> None:
        self.connect_attempted = True
        self.attempts.append(attempt)

    def deadline_status(self) -> AdbTcpTransportEnsureStatus:
        if self.attempts and self.attempts[-1].status is NativeAttemptStatus.FAILED:
            return AdbTcpTransportEnsureStatus.FAILED
        return AdbTcpTransportEnsureStatus.TIMED_OUT

    def result(
        self,
        status: AdbTcpTransportEnsureStatus,
    ) -> AdbTcpTransportEnsureResult:
        return AdbTcpTransportEnsureResult(
            operation=self.operation,
            status=status,
            satisfaction=(
                self.satisfaction
                if status is AdbTcpTransportEnsureStatus.SATISFIED
                else None
            ),
            presence_satisfaction=self.presence,
            attempts=tuple(self.attempts),
            final_transport_list=self.final_transport_list,
            final_transport=self.final_transport,
            diagnostic=self.diagnostic,
        )


class AdbTcpTransportEnsureOrchestrator:
    """Drive one configured TCP transport toward readiness before a deadline by probing
    transport lists, issuing at most one ``adb connect``, and polling to a terminal state.
    """

    def __init__(
        self,
        server: AdbServerIdentity,
        endpoint: AdbServerEndpoint,
        transport_list_reader: AdbTransportListReader,
        connector: AdbTcpConnector,
        publisher: EventPublisher,
        *,
        _monotonic: _MonotonicClock = monotonic,
        _sleep: _Sleeper = sleep,
    ) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")
        if not callable(getattr(transport_list_reader, "read", None)):
            raise TypeError("transport_list_reader must provide read()")
        if not callable(getattr(connector, "connect", None)):
            raise TypeError("connector must provide connect()")
        if not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher")
        self.server = server
        self.endpoint = endpoint
        self._transport_list_reader = transport_list_reader
        self._connector = connector
        self._publisher = publisher
        self._monotonic = _monotonic
        self._sleep = _sleep

    def ensure(
        self,
        operation: AdbTcpTransportEnsureReadiness,
    ) -> AdbTcpTransportEnsureResult:
        """Run one readiness episode and publish terminal evidence."""

        if not isinstance(operation, AdbTcpTransportEnsureReadiness):
            raise TypeError("operation must be AdbTcpTransportEnsureReadiness")
        if operation.server != self.server or operation.endpoint != self.endpoint:
            raise ValueError("operation server binding does not match ensure orchestrator")

        policy = operation.policy
        deadline = self._monotonic() + policy.timeout_seconds
        episode = _ReadinessEpisodeState(operation)

        while True:
            try:
                transport_list = self._transport_list_reader.read(self.endpoint)
            except AdbError as exc:
                episode.record_probe_failure(exc)
            else:
                terminal = episode.evaluate_transport_list(transport_list)
                if terminal is not None:
                    return self._complete(episode, terminal)

                if episode.should_attempt_connect:
                    episode.record_connect(self._connect(operation.configuration))
                    # Verify once immediately after ``adb connect``, even when the command
                    # consumed the remaining deadline.
                    continue

            remaining = deadline - self._monotonic()
            if remaining <= 0.0:
                return self._complete(episode, episode.deadline_status())
            self._sleep(min(policy.probe_interval_seconds, remaining))

    def _connect(
        self,
        configuration: AdbConfiguredTransport,
    ) -> NativeAttemptResult:
        from adb.transport.lifecycle.signal import AdbTransportCommandCompleted

        transport = configuration.transport
        if not isinstance(transport, AdbTcpTransportConfiguration):
            raise ValueError("TCP ensure requires an AdbTcpTransportConfiguration")
        operation = AdbTcpConnect(transport.connect_address)
        result = self._connector.connect(operation)
        if not isinstance(result, NativeAttemptResult):
            raise TypeError("connector must return NativeAttemptResult")
        self._publisher.publish(AdbTransportCommandCompleted(self.server, operation, result))
        return result

    def _complete(
        self,
        episode: _ReadinessEpisodeState,
        status: AdbTcpTransportEnsureStatus,
    ) -> AdbTcpTransportEnsureResult:
        from adb.transport.lifecycle.signal import AdbTcpTransportEnsureCompleted

        result = episode.result(status)
        self._publisher.publish(AdbTcpTransportEnsureCompleted(result))
        return result


__all__ = [
    "AdbTcpTransportEnsurePolicy",
    "AdbTcpTransportEnsureReadiness",
    "AdbTcpTransportEnsureResult",
    "AdbTcpTransportEnsureOrchestrator",
    "AdbTcpTransportEnsureStatus",
    "AdbTcpTransportEnsurer",
    "AdbTcpTransportPresenceSatisfaction",
    "AdbTcpTransportReadinessSatisfaction",
]
