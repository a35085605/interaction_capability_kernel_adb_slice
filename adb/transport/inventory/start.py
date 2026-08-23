from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import math
from numbers import Real
from threading import Condition
from time import monotonic

from adb.server.identity import AdbServer
from adb.transport.inventory.tracker import AdbDevicesTrackingScope
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
    """Whether server state permits starting a tracker."""

    READY = "ready"
    WAITING_FOR_SERVER = "waiting_for_server"
    INDETERMINATE = "indeterminate"


class AdbDevicesTrackingStartStatus(str, Enum):
    """Terminal status of one bounded tracker start."""

    SATISFIED = "satisfied"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStartPolicy:
    """Timeout policy for one tracker start."""

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
    """Request startup of a new tracker scope."""

    server: AdbServer
    readiness: AdbDevicesTrackingReadiness
    policy: AdbDevicesTrackingStartPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not isinstance(self.readiness, AdbDevicesTrackingReadiness):
            raise TypeError("readiness must be AdbDevicesTrackingReadiness")
        if not isinstance(self.policy, AdbDevicesTrackingStartPolicy):
            raise TypeError("policy must be AdbDevicesTrackingStartPolicy")


@dataclass(frozen=True, slots=True)
class AdbDevicesTrackingStartResult:
    """Result of one bounded tracker start."""

    operation: AdbDevicesTrackingStart
    status: AdbDevicesTrackingStartStatus
    tracking_failure: AdbDevicesTrackingFailure | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbDevicesTrackingStart):
            raise TypeError("operation must be AdbDevicesTrackingStart")
        if not isinstance(self.status, AdbDevicesTrackingStartStatus):
            raise TypeError("status must be AdbDevicesTrackingStartStatus")
        if self.tracking_failure is not None and not isinstance(
            self.tracking_failure,
            AdbDevicesTrackingFailure,
        ):
            raise TypeError("tracking_failure must be AdbDevicesTrackingFailure or None")
        if self.status is AdbDevicesTrackingStartStatus.SATISFIED:
            if self.tracking_failure is not None:
                raise ValueError("satisfied start result cannot carry tracking_failure")
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
        """Tracking start performs no ADB server mutation attempts."""

        return ()


class AdbDevicesTrackingStartOrchestrator:
    """Start a new tracker after server readiness.

    Success requires ``AdbDevicesTrackingStarted``; failure, stop, or timeout leaves
    replacement to the caller.
    """

    def __init__(
        self,
        server: AdbServer,
        event_bus: EventBus,
        tracker: AdbDevicesTrackingScope,
        *,
        _monotonic: _MonotonicClock = monotonic,
    ) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not callable(getattr(event_bus, "subscribe", None)) or not callable(
            getattr(event_bus, "unsubscribe", None)
        ):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(tracker, AdbDevicesTrackingScope):
            raise TypeError("tracker must satisfy tracking scope")
        self.server = server
        self._bus = event_bus
        self._tracker = tracker
        self._monotonic = _monotonic

    def start(
        self,
        operation: AdbDevicesTrackingStart,
    ) -> AdbDevicesTrackingStartResult:
        if not isinstance(operation, AdbDevicesTrackingStart):
            raise TypeError("operation must be AdbDevicesTrackingStart")
        if operation.server != self.server:
            raise ValueError("operation server does not match tracker server")
        if operation.readiness is not AdbDevicesTrackingReadiness.READY:
            raise RuntimeError(
                "transport-inventory tracking start requires READY server readiness"
            )

        deadline = self._monotonic() + operation.policy.timeout_seconds
        condition = Condition()
        events: deque[object] = deque()

        def collect(event: object) -> None:
            event_server = getattr(event, "server", None)
            if event_server != self.server:
                return
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
            self._tracker.start()
        except RuntimeError as exc:
            return self._complete(
                operation,
                AdbDevicesTrackingStartStatus.FAILED,
                diagnostic=str(exc),
            )

        while True:
            event = self._next_event(condition, events, deadline)
            if event is None:
                return self._complete(
                    operation,
                    AdbDevicesTrackingStartStatus.TIMED_OUT,
                    diagnostic="timed out waiting for tracking start evidence",
                )
            if isinstance(event, AdbDevicesTrackingStarted):
                return self._complete(
                    operation,
                    AdbDevicesTrackingStartStatus.SATISFIED,
                )
            if isinstance(event, AdbDevicesTrackingFailed):
                return self._complete(
                    operation,
                    AdbDevicesTrackingStartStatus.FAILED,
                    tracking_failure=event.failure,
                    diagnostic=(
                        event.diagnostic or f"tracking failed: {event.failure.value}"
                    ),
                )
            if isinstance(event, AdbDevicesTrackingStopped):
                return self._complete(
                    operation,
                    AdbDevicesTrackingStartStatus.FAILED,
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
        tracking_failure: AdbDevicesTrackingFailure | None = None,
        diagnostic: str | None = None,
    ) -> AdbDevicesTrackingStartResult:
        return AdbDevicesTrackingStartResult(
            operation=operation,
            status=status,
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
