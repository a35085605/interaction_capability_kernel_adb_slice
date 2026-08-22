from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
import math
from numbers import Real
from random import random
from threading import Lock, Thread, current_thread
from typing import TypeAlias

from adb.server.lifecycle.handle import AdbServerCloseError
from adb.server.lifecycle.launch import AdbServerLaunchError
from adb.server.failure import (
    AdbServerCloseUnprovenFailure,
    AdbServerConnectionFailure,
    AdbServerLaunchFailure,
    AdbServerLivenessFailure,
    AdbServerProcessExitedFailure,
)
from adb.server.coordination import (
    _PROCESS_ADB_SERVER_COORDINATOR,
    _AdbServerCoordination,
    _AdbServerMutationLease,
)
from adb.server.identity import AdbServer
from adb.server.ownership import AdbServerOwnershipLostError
from adb.server.endpoint import AdbServerEndpoint
from adb.server.signal import (
    AdbServerRecoveryCycleId,
    AdbServerNativeCloseCompleted,
    AdbServerNativeCloseUnproven,
    AdbServerOwnershipLost,
    AdbServerOwnershipRecovered,
    AdbServerOwnershipRetired,
    AdbServerReconciliationRequested,
    AdbServerRecoveryExhausted,
    AdbServerRecoveryRetryDue,
)
from eventing import EventBus, EventSubscriptionToken
from scheduling import ScheduleToken, TemporalScheduler


def _normalize_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return normalized


def _normalize_retry_configuration(
    *,
    retry_initial_seconds: object,
    retry_max_seconds: object,
    retry_multiplier: object,
    retry_jitter_ratio: object,
    max_attempts: object,
) -> tuple[float, float, float, float, int | None]:
    initial = _normalize_positive_seconds(
        retry_initial_seconds,
        field_name="ADB server supervision initial retry",
    )
    maximum = _normalize_positive_seconds(
        retry_max_seconds,
        field_name="ADB server supervision maximum retry",
    )
    multiplier = _normalize_positive_seconds(
        retry_multiplier,
        field_name="ADB server supervision retry multiplier",
    )
    if multiplier < 1.0:
        raise ValueError("ADB server supervision retry multiplier must be at least one")
    if maximum < initial:
        raise ValueError("ADB server supervision maximum retry must be >= initial retry")
    if isinstance(retry_jitter_ratio, bool) or not isinstance(retry_jitter_ratio, Real):
        raise TypeError("ADB server supervision retry jitter ratio must be a real number")
    jitter = float(retry_jitter_ratio)
    if not math.isfinite(jitter) or not 0.0 <= jitter < 1.0:
        raise ValueError("ADB server supervision retry jitter ratio must be in [0, 1)")
    if max_attempts is not None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise TypeError("ADB server supervision max_attempts must be an integer or None")
        if max_attempts <= 0:
            raise ValueError("ADB server supervision max_attempts must be greater than zero")
    return initial, maximum, multiplier, jitter, max_attempts


@dataclass(frozen=True, slots=True)
class AdbServerPerGenerationEndpoint:
    """Let every recovered server resolve its own endpoint independently."""


@dataclass(frozen=True, slots=True)
class AdbServerPinFirstResolvedEndpoint:
    """Pin the first server's resolved endpoint across later servers."""


@dataclass(frozen=True, slots=True)
class AdbServerFixedEndpoint:
    """Require every supervised server to use one explicitly configured endpoint."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


AdbServerEndpointPolicy: TypeAlias = (
    AdbServerPerGenerationEndpoint
    | AdbServerPinFirstResolvedEndpoint
    | AdbServerFixedEndpoint
)


def _require_endpoint_policy(value: object) -> AdbServerEndpointPolicy:
    if not isinstance(
        value,
        (
            AdbServerPerGenerationEndpoint,
            AdbServerPinFirstResolvedEndpoint,
            AdbServerFixedEndpoint,
        ),
    ):
        raise TypeError("endpoint_policy must be an ADB server endpoint policy")
    return value


@dataclass(frozen=True, slots=True)
class AdbServerSupervisionPolicy:
    """Recovery policy for reacquiring the process-coordinated owned ADB server."""

    retry_initial_seconds: float = 0.5
    retry_max_seconds: float = 30.0
    retry_multiplier: float = 2.0
    retry_jitter_ratio: float = 0.2
    max_attempts: int | None = None
    endpoint_policy: AdbServerEndpointPolicy = field(
        default_factory=AdbServerPinFirstResolvedEndpoint
    )

    def __post_init__(self) -> None:
        endpoint_policy = _require_endpoint_policy(self.endpoint_policy)
        initial, maximum, multiplier, jitter, max_attempts = _normalize_retry_configuration(
            retry_initial_seconds=self.retry_initial_seconds,
            retry_max_seconds=self.retry_max_seconds,
            retry_multiplier=self.retry_multiplier,
            retry_jitter_ratio=self.retry_jitter_ratio,
            max_attempts=self.max_attempts,
        )
        object.__setattr__(self, "endpoint_policy", endpoint_policy)
        object.__setattr__(self, "retry_initial_seconds", initial)
        object.__setattr__(self, "retry_max_seconds", maximum)
        object.__setattr__(self, "retry_multiplier", multiplier)
        object.__setattr__(self, "retry_jitter_ratio", jitter)
        object.__setattr__(self, "max_attempts", max_attempts)


_RandomSource = Callable[[], float]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


def _require_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a bool")
    return value


class AdbServerSupervisor:
    """Maintain durable intent for active process-owned ADB server ownership.

    The supervised resource itself does not cross a failure boundary. Terminal liveness
    evidence first retires the current :class:`AdbServer` and publishes
    :class:`AdbServerOwnershipRetired`, so managed dependents can immediately tear down their
    old server-bound scopes. Native close then proceeds privately. Endpoint continuity policy
    decides whether recovery reuses an endpoint and therefore waits for proven close, or lets the
    next server resolve an independent endpoint while retired teardown continues.

    Existing listeners never satisfy recovery because the owned lifetime store only accepts a
    native handle returned by its launcher. Retry cycle IDs fence scheduled retry work only;
    each :class:`AdbServer` carries a separate server identity.
    """

    def __init__(
        self,
        server: AdbServer,
        event_bus: EventBus,
        scheduler: TemporalScheduler[object],
        policy: AdbServerSupervisionPolicy,
        *,
        _coordination: _AdbServerCoordination = _PROCESS_ADB_SERVER_COORDINATOR,
        _random: _RandomSource = random,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(_coordination, _AdbServerCoordination):
            raise TypeError("_coordination must satisfy _AdbServerCoordination")
        if not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler")
        if not isinstance(policy, AdbServerSupervisionPolicy):
            raise TypeError("policy must be AdbServerSupervisionPolicy")

        endpoint_policy = policy.endpoint_policy
        if isinstance(endpoint_policy, AdbServerFixedEndpoint):
            if server.endpoint != endpoint_policy.endpoint:
                raise ValueError(
                    "fixed endpoint policy must match the initially supervised server"
                )
            pinned_endpoint: AdbServerEndpoint | None = endpoint_policy.endpoint
        elif isinstance(endpoint_policy, AdbServerPinFirstResolvedEndpoint):
            pinned_endpoint = server.endpoint
        else:
            pinned_endpoint = None

        self.server: AdbServer | None = server
        self.endpoint = server.endpoint
        self._pinned_endpoint = pinned_endpoint
        self._bus = event_bus
        self._coordination = _coordination
        self._scheduler = scheduler
        self._policy = policy
        self._random = _random
        self._thread_factory = _thread_factory

        self._lock = Lock()
        self._mutation_lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._desired_running = False
        self._recovery_enabled = False
        self._cycle_id: AdbServerRecoveryCycleId | None = None
        self._retry_token: ScheduleToken | None = None
        self._closing_server: AdbServer | None = None
        self._pending_retired_disposals: set[AdbServer] = set()
        self._attempt_threads: set[Thread] = set()
        self._closed = False
        self._mutation_lease: _AdbServerMutationLease = (
            self._coordination.claim_mutation_authority(server)
        )

    @property
    def desired_running(self) -> bool:
        with self._lock:
            return self._desired_running

    @property
    def recovery_enabled(self) -> bool:
        with self._lock:
            return self._recovery_enabled

    def start(self, *, recovery_enabled: bool) -> None:
        """Arm managed intent around the current server or its future recreation."""

        enabled = _require_bool(recovery_enabled, field_name="recovery_enabled")
        launch_cycle: AdbServerRecoveryCycleId | None = None
        with self._mutation_lock:
            with self._lock:
                self._require_open()
                old_token = self._invalidate_recovery_locked()
                server = self.server
                if server is not None and self._coordination.active_server != server:
                    self.server = None
                    server = None
                if server is None and not enabled:
                    raise AdbServerOwnershipLostError(
                        "cannot start supervision without an active owned server when recovery is disabled"
                    )
                self._desired_running = True
                self._recovery_enabled = enabled
                self._ensure_subscriptions_locked()
                if (
                    server is None
                    and enabled
                    and self._closing_server is None
                ):
                    launch_cycle = self._new_recovery_cycle_locked()
            if old_token is not None:
                self._scheduler.cancel(old_token)
        if launch_cycle is not None:
            self._launch_recovery_attempt(launch_cycle, attempt_number=1)

    def stop(self) -> None:
        """Disarm managed intent without terminating any native ADB server."""

        with self._mutation_lock:
            with self._lock:
                self._require_open()
                old_token = self._invalidate_recovery_locked()
                self._desired_running = False
                self._recovery_enabled = False
            if old_token is not None:
                self._scheduler.cancel(old_token)

    def set_recovery_enabled(self, enabled: bool) -> None:
        """Enable or disable automatic ownership recovery.

        Enabling recovery while managed ownership is already absent may start a recovery attempt
        immediately.
        """

        normalized = _require_bool(enabled, field_name="enabled")
        launch_cycle: AdbServerRecoveryCycleId | None = None
        with self._mutation_lock:
            with self._lock:
                self._require_open()
                if normalized and not self._desired_running:
                    raise RuntimeError(
                        "cannot enable ADB server recovery without managed running intent"
                    )
                if self._recovery_enabled is normalized:
                    return
                old_token = self._invalidate_recovery_locked()
                self._recovery_enabled = normalized
                if (
                    normalized
                    and self.server is None
                    and self._closing_server is None
                ):
                    launch_cycle = self._new_recovery_cycle_locked()
            if old_token is not None:
                self._scheduler.cancel(old_token)
        if launch_cycle is not None:
            self._launch_recovery_attempt(launch_cycle, attempt_number=1)

    def reconcile(self, failure: AdbServerLivenessFailure) -> None:
        """Retire the current server from terminal liveness evidence and reconcile intent."""

        if not isinstance(
            failure,
            (AdbServerConnectionFailure, AdbServerProcessExitedFailure),
        ):
            raise TypeError(
                "failure must be AdbServerConnectionFailure or "
                "AdbServerProcessExitedFailure"
            )
        self._invalidate_owner_and_maybe_recover(failure)

    def close(self) -> None:
        """Stop supervising without retiring or terminating the healthy current owned server.

        Retired generations that have not yet handed teardown to a worker are adopted by close.
        Already-started teardown is joined before mutation authority is released. This makes the
        pending-to-started handoff atomic with respect to close, including when close is invoked
        synchronously by an ownership-retirement event handler.
        """

        with self._mutation_lock:
            with self._lock:
                if self._closed:
                    return
                self._closed = True
                self._desired_running = False
                self._recovery_enabled = False
                subscriptions = self._subscriptions
                self._subscriptions = ()
                retry_token = self._invalidate_recovery_locked()
                pending_retired = tuple(self._pending_retired_disposals)
                self._pending_retired_disposals.clear()
                attempt_threads = tuple(self._attempt_threads)
        try:
            for token in subscriptions:
                self._bus.unsubscribe(token)
            if retry_token is not None:
                self._scheduler.cancel(retry_token)
            for server in pending_retired:
                self._dispose_retired_server(server)
            for thread in attempt_threads:
                if thread is not current_thread():
                    thread.join()
        finally:
            self._coordination.release_mutation_authority(self._mutation_lease)

    def _invalidate_owner_and_maybe_recover(
        self,
        failure: AdbServerLivenessFailure,
    ) -> None:
        retired_server: AdbServer | None = None
        launch_cycle: AdbServerRecoveryCycleId | None = None

        with self._mutation_lock:
            with self._lock:
                self._require_open()
                if not self._desired_running:
                    return
                server = self.server

            if server is not None:
                self._coordination.retire_server(server, lease=self._mutation_lease)
                with self._lock:
                    self.server = None
                    if self._requires_retired_close_before_launch():
                        self._closing_server = server
                    retired_server = server
                    self._pending_retired_disposals.add(server)
                    if (
                        not self._requires_retired_close_before_launch()
                        and self._recovery_enabled
                        and self._cycle_id is None
                    ):
                        launch_cycle = self._new_recovery_cycle_locked()

        if retired_server is None:
            return

        # Public ownership disappears before native close begins. Dependents use the neutral
        # retirement fact for teardown; loss remains separate failure evidence.
        try:
            self._bus.publish(
                AdbServerOwnershipRetired(retired_server)
            )
            self._bus.publish(
                AdbServerOwnershipLost(
                    retired_server,
                    failure,
                )
            )
        finally:
            self._launch_retired_disposal(retired_server)
            if launch_cycle is not None:
                self._launch_recovery_attempt(launch_cycle, attempt_number=1)

    def _launch_retired_disposal(self, server: AdbServer) -> None:
        with self._mutation_lock:
            with self._lock:
                if server not in self._pending_retired_disposals:
                    return
                thread = self._thread_factory(
                    target=self._run_retired_disposal,
                    args=(server,),
                    name=(
                        "adb-server-close-"
                        f"{server.endpoint.host}-{server.endpoint.port}-{server.epoch}"
                    ),
                )
                self._attempt_threads.add(thread)
                try:
                    thread.start()
                except BaseException:
                    self._attempt_threads.discard(thread)
                    raise
                else:
                    self._pending_retired_disposals.remove(server)

    def _run_retired_disposal(self, server: AdbServer) -> None:
        active = current_thread()
        try:
            self._dispose_retired_server(server)
        finally:
            with self._lock:
                self._attempt_threads.discard(active)

    def _dispose_retired_server(self, server: AdbServer) -> None:
        try:
            self._coordination.dispose_retired(server, lease=self._mutation_lease)
        except AdbServerCloseError as exc:
            self._bus.publish(
                AdbServerNativeCloseUnproven(
                    server,
                    AdbServerCloseUnprovenFailure(str(exc)),
                )
            )
            return

        try:
            self._bus.publish(
                AdbServerNativeCloseCompleted(server)
            )
        finally:
            launch_cycle: AdbServerRecoveryCycleId | None = None
            with self._mutation_lock:
                with self._lock:
                    if self._closing_server == server:
                        self._closing_server = None
                    if (
                        not self._closed
                        and self._desired_running
                        and self._recovery_enabled
                        and self.server is None
                        and self._closing_server is None
                        and self._cycle_id is None
                    ):
                        launch_cycle = self._new_recovery_cycle_locked()
            if launch_cycle is not None:
                self._launch_recovery_attempt(launch_cycle, attempt_number=1)

    def _launch_recovery_attempt(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
    ) -> None:
        thread = self._thread_factory(
            target=self._run_recovery_attempt,
            args=(cycle_id, attempt_number),
            name=(
                "adb-server-recovery-"
                f"{self.endpoint.host}-{self.endpoint.port}-{attempt_number}"
            ),
        )
        with self._lock:
            if not self._recovery_is_current_locked(cycle_id):
                return
            self._attempt_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._attempt_threads.discard(thread)
                raise

    def _run_recovery_attempt(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
    ) -> None:
        active = current_thread()
        launch_failure: AdbServerLaunchFailure | None = None
        recovered_event: AdbServerOwnershipRecovered | None = None
        retry_token: ScheduleToken | None = None
        try:
            with self._mutation_lock:
                with self._lock:
                    if not self._recovery_is_current_locked(cycle_id):
                        return
                try:
                    recovered = self._coordination.acquire_server(
                        self._recovery_launch_endpoint(),
                        lease=self._mutation_lease,
                    )
                except AdbServerLaunchError as exc:
                    launch_failure = AdbServerLaunchFailure(str(exc))
                else:
                    expected_endpoint = self._recovery_launch_endpoint()
                    if expected_endpoint is not None and recovered.endpoint != expected_endpoint:
                        raise ValueError("endpoint-pinned server recovery changed endpoint")
                    with self._lock:
                        if not self._recovery_is_current_locked(cycle_id):
                            return
                        retry_token = self._retry_token
                        self._retry_token = None
                        self._cycle_id = None
                        self.server = recovered
                        self.endpoint = recovered.endpoint
                        recovered_event = AdbServerOwnershipRecovered(recovered)

            if recovered_event is not None:
                if retry_token is not None:
                    self._scheduler.cancel(retry_token)
                self._bus.publish(recovered_event)
                return

            if launch_failure is not None:
                with self._lock:
                    if not self._recovery_is_current_locked(cycle_id):
                        return
                self._schedule_retry_or_exhaust(
                    cycle_id,
                    attempt_number,
                    launch_failure,
                )
        finally:
            with self._lock:
                self._attempt_threads.discard(active)

    def _schedule_retry_or_exhaust(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
        failure: AdbServerLaunchFailure,
    ) -> None:
        max_attempts = self._policy.max_attempts
        if max_attempts is not None and attempt_number >= max_attempts:
            self._end_recovery_cycle(cycle_id)
            self._bus.publish(
                AdbServerRecoveryExhausted(
                    self.endpoint,
                    cycle_id,
                    attempt_number,
                    failure,
                )
            )
            return

        next_attempt = attempt_number + 1
        delay_seconds = self._retry_delay(attempt_number)
        retry_event = AdbServerRecoveryRetryDue(
            self.endpoint,
            cycle_id,
            next_attempt,
        )
        token = self._scheduler.schedule_after(
            timedelta(seconds=delay_seconds),
            retry_event,
        )
        with self._lock:
            if not self._recovery_is_current_locked(cycle_id):
                self._scheduler.cancel(token)
                return
            old_token = self._retry_token
            self._retry_token = token
        if old_token is not None:
            self._scheduler.cancel(old_token)

    def _ensure_subscriptions_locked(self) -> None:
        if self._subscriptions:
            return
        reconciliation_subscription = self._bus.subscribe(
            AdbServerReconciliationRequested,
            self._on_reconciliation_requested,
        )
        retry_subscription = self._bus.subscribe(
            AdbServerRecoveryRetryDue,
            self._on_retry_due,
        )
        self._subscriptions = (reconciliation_subscription, retry_subscription)

    def _on_reconciliation_requested(
        self,
        event: AdbServerReconciliationRequested,
    ) -> None:
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            server = self.server
            if server is None or server != event.server:
                return
        self._invalidate_owner_and_maybe_recover(event.failure)

    def _on_retry_due(self, event: AdbServerRecoveryRetryDue) -> None:
        if event.endpoint != self.endpoint:
            return
        with self._lock:
            if not self._recovery_is_current_locked(event.cycle_id):
                return
            self._retry_token = None
        self._launch_recovery_attempt(event.cycle_id, event.attempt_number)

    def _new_recovery_cycle_locked(self) -> AdbServerRecoveryCycleId:
        if self._cycle_id is not None:
            return self._cycle_id
        cycle_id = AdbServerRecoveryCycleId.new()
        self._cycle_id = cycle_id
        return cycle_id

    def _end_recovery_cycle(self, cycle_id: AdbServerRecoveryCycleId) -> None:
        with self._lock:
            if self._cycle_id != cycle_id:
                return
            retry_token = self._retry_token
            self._retry_token = None
            self._cycle_id = None
        if retry_token is not None:
            self._scheduler.cancel(retry_token)

    def _invalidate_recovery_locked(self) -> ScheduleToken | None:
        retry_token = self._retry_token
        self._retry_token = None
        self._cycle_id = None
        return retry_token

    def _recovery_is_current_locked(self, cycle_id: AdbServerRecoveryCycleId) -> bool:
        return (
            not self._closed
            and self._desired_running
            and self._recovery_enabled
            and self.server is None
            and self._closing_server is None
            and self._cycle_id == cycle_id
        )

    def _requires_retired_close_before_launch(self) -> bool:
        return not isinstance(
            self._policy.endpoint_policy,
            AdbServerPerGenerationEndpoint,
        )

    def _recovery_launch_endpoint(self) -> AdbServerEndpoint | None:
        return self._pinned_endpoint

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("ADB server supervisor is closed")

    def _retry_delay(self, attempt_number: int) -> float:
        base = min(
            self._policy.retry_initial_seconds
            * (self._policy.retry_multiplier ** max(0, attempt_number - 1)),
            self._policy.retry_max_seconds,
        )
        jitter = self._policy.retry_jitter_ratio
        sample = self._random()
        if not 0.0 <= sample <= 1.0:
            raise ValueError("server supervision random source must return a value in [0, 1]")
        factor = 1.0 + ((sample * 2.0) - 1.0) * jitter
        return max(base * factor, 1e-6)


__all__ = [
    "AdbServerEndpointPolicy",
    "AdbServerFixedEndpoint",
    "AdbServerPerGenerationEndpoint",
    "AdbServerPinFirstResolvedEndpoint",
    "AdbServerRecoveryCycleId",
    "AdbServerSupervisionPolicy",
    "AdbServerSupervisor",
]
