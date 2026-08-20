from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import math
from numbers import Real
from threading import Condition
from time import monotonic

from adb.server.endpoint import AdbServerEndpoint
from adb.transport.inventory.model import AdbDevicesTrackingSessionId
from adb.transport.inventory.tracker import AdbDevicesTrackingController
from adb.transport.signal import (
    AdbDevicesTrackingFailed,
    AdbDevicesTrackingFailure,
    AdbDevicesTrackingStarted,
    AdbDevicesTrackingStopped,
)
from eventing import EventBus, EventSubscriptionToken
from native_attempt import NativeAttemptResult


_MonotonicClock = Callable[[], float]


def _normalize_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
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


class AdbDevicesTrackingReadiness(str, Enum):
    """Whether fresh upstream server evidence permits a new tracking generation."""

    READY = "ready"
    WAITING_FOR_SERVER = "waiting_for_server"
    INDETERMINATE = "indeterminate"


class AdbDevicesTrackingStartStatus(str, Enum):
    """Terminal status of one bounded transport-inventory tracking start episode."""

    SATISFIED = "satisfied"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStartPolicy:
    """Bound one transport-inventory tracking start episode after readiness."""

    timeout_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timeout_seconds",
            _normalize_positive_seconds(
                self.timeout_seconds,
                field_name="ADB transport-inventory tracking start timeout",
            ),
        )


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStart:
    """Request one tracking generation after upstream server readiness is established."""

    endpoint: AdbServerEndpoint
    readiness: AdbDevicesTrackingReadiness
    policy: AdbDevicesTrackingStartPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(
            self.readiness,
            AdbDevicesTrackingReadiness,
        ):
            raise TypeError(
                "readiness must be AdbDevicesTrackingReadiness"
            )
        if not isinstance(
            self.policy,
            AdbDevicesTrackingStartPolicy,
        ):
            raise TypeError(
                "policy must be AdbDevicesTrackingStartPolicy"
            )


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStartResult:
    """Evidence from one bounded transport-inventory tracking start episode."""

    operation: AdbDevicesTrackingStart
    status: AdbDevicesTrackingStartStatus
    tracking_session_id: AdbDevicesTrackingSessionId | None = None
    tracking_failure: AdbDevicesTrackingFailure | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(
            self.operation,
            AdbDevicesTrackingStart,
        ):
            raise TypeError(
                "operation must be AdbDevicesTrackingStart"
            )
        if not isinstance(
            self.status,
            AdbDevicesTrackingStartStatus,
        ):
            raise TypeError(
                "status must be AdbDevicesTrackingStartStatus"
            )
        if self.tracking_session_id is not None:
            if not isinstance(self.tracking_session_id, AdbDevicesTrackingSessionId):
                raise TypeError("tracking_session_id must be AdbDevicesTrackingSessionId or None")
            if self.tracking_session_id.endpoint != self.operation.endpoint:
                raise ValueError(
                    "tracking session endpoint must match start operation"
                )
        if self.tracking_failure is not None and not isinstance(
            self.tracking_failure,
            AdbDevicesTrackingFailure,
        ):
            raise TypeError(
                "tracking_failure must be AdbDevicesTrackingFailure or None"
            )
        if self.status is AdbDevicesTrackingStartStatus.SATISFIED:
            if self.tracking_session_id is None:
                raise ValueError(
                    "satisfied start result requires tracking_session_id"
                )
            if self.tracking_failure is not None:
                raise ValueError(
                    "satisfied start result cannot carry tracking_failure"
                )
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="ADB transport-inventory tracking start diagnostic",
            ),
        )

    @property
    def attempts(self) -> tuple[NativeAttemptResult, ...]:
        """Tracking start performs no native server mutation attempts."""

        return ()


class AdbDevicesTrackingStartOrchestrator:
    """Start one track-devices tracking generation after server readiness.

    The caller must provide ``READY`` readiness projected from fresh upstream server evidence.
    Only after that precondition is satisfied does the bounded start deadline begin.
    The episode owns no retry/backoff or server-lifecycle policy. Satisfaction requires matching
    ``AdbDevicesTrackingStarted`` evidence, not merely acceptance of ``tracking.start()``.
    Server condition maintenance belongs to ``AdbServerSupervisor``.
    """

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        event_bus: EventBus,
        tracker: AdbDevicesTrackingController,
        *,
        _monotonic: _MonotonicClock = monotonic,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not callable(getattr(event_bus, "subscribe", None)) or not callable(
            getattr(event_bus, "unsubscribe", None)
        ):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(tracker, AdbDevicesTrackingController):
            raise TypeError("tracker must satisfy tracking controller")
        self.endpoint = endpoint
        self._bus = event_bus
        self._tracker = tracker
        self._monotonic = _monotonic

    def start(
        self,
        operation: AdbDevicesTrackingStart,
    ) -> AdbDevicesTrackingStartResult:
        if not isinstance(
            operation,
            AdbDevicesTrackingStart,
        ):
            raise TypeError(
                "operation must be AdbDevicesTrackingStart"
            )
        if operation.endpoint != self.endpoint:
            raise ValueError("operation endpoint does not match configured ADB server endpoint")
        if operation.readiness is not AdbDevicesTrackingReadiness.READY:
            raise RuntimeError(
                "transport-inventory tracking start requires READY server readiness"
            )

        # The start clock deliberately starts only after upstream readiness has been
        # established. WAITING_FOR_SERVER and INDETERMINATE carry no tracking deadline.
        deadline = self._monotonic() + operation.policy.timeout_seconds
        condition = Condition()
        events: deque[object] = deque()

        def collect(event: object) -> None:
            with condition:
                events.append(event)
                condition.notify()

        subscriptions = self._subscribe(collect)
        try:
            return self._run_episode(operation, deadline, condition, events)
        finally:
            for token in subscriptions:
                self._bus.unsubscribe(token)

    def _run_episode(
        self,
        operation: AdbDevicesTrackingStart,
        deadline: float,
        condition: Condition,
        events: deque[object],
    ) -> AdbDevicesTrackingStartResult:
        if deadline - self._monotonic() <= 0.0:
            return self._complete(
                operation,
                AdbDevicesTrackingStartStatus.TIMED_OUT,
                diagnostic="start deadline expired before tracking start",
            )

        try:
            session_id = self._tracker.start()
        except RuntimeError as exc:
            return self._complete(
                operation,
                AdbDevicesTrackingStartStatus.FAILED,
                diagnostic=str(exc),
            )
        if session_id.endpoint != operation.endpoint:
            raise ValueError("started tracking belongs to another ADB server endpoint")

        while True:
            event = self._next_event(condition, events, deadline)
            if event is None:
                return self._complete(
                    operation,
                    AdbDevicesTrackingStartStatus.TIMED_OUT,
                    tracking_session_id=session_id,
                    diagnostic="timed out waiting for tracking start evidence",
                )

            event_session = getattr(event, "session_id", None)
            if event_session != session_id:
                continue
            if isinstance(event, AdbDevicesTrackingStarted):
                return self._complete(
                    operation,
                    AdbDevicesTrackingStartStatus.SATISFIED,
                    tracking_session_id=session_id,
                )
            if isinstance(event, AdbDevicesTrackingFailed):
                return self._complete(
                    operation,
                    AdbDevicesTrackingStartStatus.FAILED,
                    tracking_session_id=session_id,
                    tracking_failure=event.failure,
                    diagnostic=(
                        event.diagnostic or f"tracking failed: {event.failure.value}"
                    ),
                )
            if isinstance(event, AdbDevicesTrackingStopped):
                return self._complete(
                    operation,
                    AdbDevicesTrackingStartStatus.FAILED,
                    tracking_session_id=session_id,
                    diagnostic="tracking stopped before start",
                )

    def _subscribe(
        self,
        collect: Callable[[object], None],
    ) -> tuple[EventSubscriptionToken, ...]:
        return (
            self._bus.subscribe(AdbDevicesTrackingStarted, collect),
            self._bus.subscribe(AdbDevicesTrackingFailed, collect),
            self._bus.subscribe(AdbDevicesTrackingStopped, collect),
        )

    def _next_event(
        self,
        condition: Condition,
        events: deque[object],
        deadline: float,
    ) -> object | None:
        with condition:
            while not events:
                remaining = deadline - self._monotonic()
                if remaining <= 0.0:
                    return None
                condition.wait(timeout=remaining)
            return events.popleft()

    @staticmethod
    def _complete(
        operation: AdbDevicesTrackingStart,
        status: AdbDevicesTrackingStartStatus,
        *,
        tracking_session_id: AdbDevicesTrackingSessionId | None = None,
        tracking_failure: AdbDevicesTrackingFailure | None = None,
        diagnostic: str | None = None,
    ) -> AdbDevicesTrackingStartResult:
        return AdbDevicesTrackingStartResult(
            operation=operation,
            status=status,
            tracking_session_id=tracking_session_id,
            tracking_failure=tracking_failure,
            diagnostic=diagnostic,
        )


__all__ = [
    "AdbDevicesTrackingStart",
    "AdbDevicesTrackingStartOrchestrator",
    "AdbDevicesTrackingStartPolicy",
    "AdbDevicesTrackingReadiness",
    "AdbDevicesTrackingStartResult",
    "AdbDevicesTrackingStartStatus",
]
