from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
import math
from numbers import Real
from time import monotonic, sleep
from typing import Protocol, TypeAlias, runtime_checkable

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
    """Configure bounded polling for transport readiness."""

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
    """Request readiness verification for a transport on one server lifetime."""

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
    """Ensure readiness for configured TCP transports with independent concurrent episodes."""

    def ensure(
        self,
        operation: AdbTcpTransportEnsureReadiness,
    ) -> AdbTcpTransportEnsureResult:
        """Ensure one configured TCP transport reaches a terminal readiness result."""
        ...


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class AdbTcpTransportEnsureProbe:
    """Instruction to probe the bound server transport list after an optional delay."""

    delay_seconds: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.delay_seconds, bool) or not isinstance(self.delay_seconds, Real):
            raise TypeError("delay_seconds must be a real number")
        delay = float(self.delay_seconds)
        if not math.isfinite(delay) or delay < 0.0:
            raise ValueError("delay_seconds must be finite and greater than or equal to zero")
        object.__setattr__(self, "delay_seconds", delay)


@dataclass(frozen=True, slots=True)
class AdbTcpTransportEnsureConnect:
    """Instruction to issue the single TCP connect attempt allowed by one ensure episode."""


@dataclass(frozen=True, slots=True)
class AdbTcpTransportEnsureCompleted:
    """Terminal instruction carrying the canonical evidence for one ensure episode."""

    result: AdbTcpTransportEnsureResult

    def __post_init__(self) -> None:
        if not isinstance(self.result, AdbTcpTransportEnsureResult):
            raise TypeError("result must be AdbTcpTransportEnsureResult")


AdbTcpTransportEnsureInstruction: TypeAlias = (
    AdbTcpTransportEnsureProbe
    | AdbTcpTransportEnsureConnect
    | AdbTcpTransportEnsureCompleted
)


@dataclass(slots=True)
class AdbTcpTransportEnsureEpisode:
    """Decision state for one bounded TCP transport-readiness episode.

    Interprets readiness evidence, selects connect attempts, and determines terminal
    results. The orchestrator executes its instructions and feeds observations back
    into the episode.
    """

    operation: AdbTcpTransportEnsureReadiness
    attempts: list[NativeAttemptResult] = field(default_factory=list, init=False)
    presence: AdbTcpTransportPresenceSatisfaction | None = field(default=None, init=False)
    satisfaction: AdbTcpTransportReadinessSatisfaction | None = field(default=None, init=False)
    final_transport_list: AdbTransportList | None = field(default=None, init=False)
    final_transport: AdbTransport | None = field(default=None, init=False)
    diagnostic: str | None = field(default=None, init=False)
    probes_attempted: int = field(default=0, init=False)
    connect_attempted: bool = field(default=False, init=False)
    latest_resolution_status: AdbConfiguredTransportResolutionStatus | None = field(
        default=None, init=False
    )
    _started: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbTcpTransportEnsureReadiness):
            raise TypeError("operation must be AdbTcpTransportEnsureReadiness")

    @property
    def policy(self) -> AdbTcpTransportEnsurePolicy:
        return self.operation.policy

    def begin(self) -> AdbTcpTransportEnsureInstruction:
        """Select the first immediate transport-list probe for this episode."""

        if self._started:
            raise RuntimeError("ADB TCP transport ensure episode has already begun")
        self._started = True
        return AdbTcpTransportEnsureProbe()

    def decide_after_probe_failure(
        self,
        error: AdbError,
    ) -> AdbTcpTransportEnsureInstruction:
        """Apply one transport-list probe failure and select the next probe."""

        self._require_started()
        if not isinstance(error, AdbError):
            raise TypeError("error must be AdbError")
        self.probes_attempted += 1
        self.latest_resolution_status = None
        self.diagnostic = str(error) or error.__class__.__name__
        return AdbTcpTransportEnsureProbe(self.policy.probe_interval_seconds)

    def decide_after_probe(
        self,
        transport_list: AdbTransportList,
    ) -> AdbTcpTransportEnsureInstruction:
        """Interpret one transport-list observation and select the next instruction."""

        self._require_started()
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

        terminal = self._evaluate_transport_list(transport_list)
        if terminal is not None:
            return self._complete(terminal)
        if self._should_attempt_connect:
            return AdbTcpTransportEnsureConnect()
        return AdbTcpTransportEnsureProbe(self.policy.probe_interval_seconds)

    def decide_after_connect(
        self,
        attempt: NativeAttemptResult,
    ) -> AdbTcpTransportEnsureInstruction:
        """Record the selected connect effect and require one immediate verification probe."""

        self._require_started()
        if not isinstance(attempt, NativeAttemptResult):
            raise TypeError("attempt must be NativeAttemptResult")
        if not self._should_attempt_connect:
            raise RuntimeError("ADB TCP transport ensure connect was not selected")
        self.connect_attempted = True
        self.attempts.append(attempt)
        # Preserve the existing contract: always verify once immediately after ``adb connect``,
        # even when the command consumed the remaining deadline.
        return AdbTcpTransportEnsureProbe()

    def _evaluate_transport_list(
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
    def _should_attempt_connect(self) -> bool:
        return (
            self.latest_resolution_status
            is AdbConfiguredTransportResolutionStatus.ABSENT
            and self.presence is None
            and not self.connect_attempted
        )

    def decide_deadline(self) -> AdbTcpTransportEnsureCompleted:
        """Select terminal timeout/failure evidence after the orchestrator observes the deadline."""

        self._require_started()
        return self._complete(self._deadline_status())

    def _deadline_status(self) -> AdbTcpTransportEnsureStatus:
        if self.attempts and self.attempts[-1].status is NativeAttemptStatus.FAILED:
            return AdbTcpTransportEnsureStatus.FAILED
        return AdbTcpTransportEnsureStatus.TIMED_OUT

    def _complete(
        self,
        status: AdbTcpTransportEnsureStatus,
    ) -> AdbTcpTransportEnsureCompleted:
        return AdbTcpTransportEnsureCompleted(
            AdbTcpTransportEnsureResult(
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
        )

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("ADB TCP transport ensure episode has not begun")


class AdbTcpTransportEnsureOrchestrator:
    """Drive a configured TCP transport to a terminal readiness result before its deadline.

    Probes transport lists and may issue one ``adb connect`` while polling.
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
        episode = AdbTcpTransportEnsureEpisode(operation)
        instruction = episode.begin()

        while True:
            if isinstance(instruction, AdbTcpTransportEnsureCompleted):
                return self._complete(instruction.result)

            if isinstance(instruction, AdbTcpTransportEnsureConnect):
                instruction = episode.decide_after_connect(
                    self._connect(operation.configuration)
                )
                continue

            if not isinstance(instruction, AdbTcpTransportEnsureProbe):
                raise TypeError("ensure episode returned an unsupported instruction")

            if instruction.delay_seconds > 0.0:
                remaining = deadline - self._monotonic()
                if remaining <= 0.0:
                    instruction = episode.decide_deadline()
                    continue
                self._sleep(min(instruction.delay_seconds, remaining))

            try:
                transport_list = self._transport_list_reader.read(self.endpoint)
            except AdbError as exc:
                instruction = episode.decide_after_probe_failure(exc)
            else:
                instruction = episode.decide_after_probe(transport_list)

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
        result: AdbTcpTransportEnsureResult,
    ) -> AdbTcpTransportEnsureResult:
        from adb.transport.lifecycle.signal import AdbTcpTransportEnsureCompleted

        if not isinstance(result, AdbTcpTransportEnsureResult):
            raise TypeError("result must be AdbTcpTransportEnsureResult")
        self._publisher.publish(AdbTcpTransportEnsureCompleted(result))
        return result


__all__ = [
    "AdbTcpTransportEnsureCompleted",
    "AdbTcpTransportEnsureConnect",
    "AdbTcpTransportEnsureEpisode",
    "AdbTcpTransportEnsureInstruction",
    "AdbTcpTransportEnsureOrchestrator",
    "AdbTcpTransportEnsurePolicy",
    "AdbTcpTransportEnsureProbe",
    "AdbTcpTransportEnsureReadiness",
    "AdbTcpTransportEnsureResult",
    "AdbTcpTransportEnsureStatus",
    "AdbTcpTransportEnsurer",
    "AdbTcpTransportPresenceSatisfaction",
    "AdbTcpTransportReadinessSatisfaction",
]
