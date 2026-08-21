from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from random import random
from threading import Lock, Thread, current_thread

from adb.server.lifecycle.native import AdbServerLaunchError
from adb.server.failure import (
    AdbServerCloseUnprovenFailure,
    AdbServerConnectionFailure,
    AdbServerLaunchFailure,
    AdbServerOwnershipLossFailure,
    AdbServerProcessExitedFailure,
)
from adb.server.ownership import (
    AdbOwnedServer,
    AdbServerOwnershipLostError,
    _PROCESS_ADB_SERVER_OWNER,
    _ProcessAdbServerOwner,
)
from adb.supervision.model import AdbServerRecoveryCycleId, AdbServerSupervisionPolicy
from adb.supervision.signal import (
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
    evidence first retires the current :class:`AdbOwnedServer` and publishes
    :class:`AdbServerOwnershipRetired`, so managed dependents can immediately tear down their
    old server-bound scopes. Native close then proceeds privately; recovery cannot reacquire a
    fresh generation until termination of the retired lifetime is proven.

    Existing listeners never satisfy recovery because the process owner can only acquire a
    native handle returned by its launcher. Retry cycle IDs fence scheduled retry work only;
    each :class:`AdbOwnedServer` carries a separate native generation identity.
    """

    def __init__(
        self,
        server: AdbOwnedServer,
        event_bus: EventBus,
        scheduler: TemporalScheduler[object],
        policy: AdbServerSupervisionPolicy,
        *,
        _owner_manager: _ProcessAdbServerOwner = _PROCESS_ADB_SERVER_OWNER,
        _random: _RandomSource = random,
        _thread_factory: _ThreadFactory = _default_thread_factory,
    ) -> None:
        if not isinstance(server, AdbOwnedServer):
            raise TypeError("server must be AdbOwnedServer")
        if not callable(getattr(event_bus, "publish", None)) or not callable(
            getattr(event_bus, "subscribe", None)
        ) or not callable(getattr(event_bus, "unsubscribe", None)):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(_owner_manager, _ProcessAdbServerOwner):
            raise TypeError("_owner_manager must be _ProcessAdbServerOwner")
        if _owner_manager.active_owner is not server:
            raise ValueError("server must be the process owner's active generation")
        if not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler")
        if not isinstance(policy, AdbServerSupervisionPolicy):
            raise TypeError("policy must be AdbServerSupervisionPolicy")

        self.server: AdbOwnedServer | None = server
        self.endpoint = server.endpoint
        self._bus = event_bus
        self._owner_manager = _owner_manager
        self._scheduler = scheduler
        self._policy = policy
        self._random = _random
        self._thread_factory = _thread_factory

        self._lock = Lock()
        self._mutation_lock = Lock()
        self._subscriptions: tuple[EventSubscriptionToken, ...] = ()
        self._desired_running = False
        self._recovery_enabled = False
        self._recovery_epoch = 0
        self._cycle_id: AdbServerRecoveryCycleId | None = None
        self._retry_token: ScheduleToken | None = None
        self._closing_generation: int | None = None
        self._attempt_threads: set[Thread] = set()
        self._closed = False

    @property
    def desired_running(self) -> bool:
        with self._lock:
            return self._desired_running

    @property
    def recovery_enabled(self) -> bool:
        with self._lock:
            return self._recovery_enabled

    @property
    def recovery_epoch(self) -> int:
        with self._lock:
            return self._recovery_epoch

    def start(self, *, recovery_enabled: bool) -> None:
        """Arm managed intent around the current owner or its future recreation."""

        enabled = _require_bool(recovery_enabled, field_name="recovery_enabled")
        launch_cycle: AdbServerRecoveryCycleId | None = None
        with self._mutation_lock:
            with self._lock:
                self._require_open()
                old_token = self._invalidate_recovery_locked()
                server = self.server
                if server is not None and self._owner_manager.active_owner is not server:
                    self.server = None
                    server = None
                if server is None and not enabled:
                    raise AdbServerOwnershipLostError(
                        "cannot start supervision without an active owner when recovery is disabled"
                    )
                self._desired_running = True
                self._recovery_enabled = enabled
                self._recovery_epoch += 1
                self._ensure_subscriptions_locked()
                if (
                    server is None
                    and enabled
                    and self._closing_generation is None
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
                self._recovery_epoch += 1
            if old_token is not None:
                self._scheduler.cancel(old_token)

    def set_recovery_enabled(self, enabled: bool) -> None:
        """Enable or disable future recreation after ownership has been invalidated."""

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
                self._recovery_epoch += 1
                if (
                    normalized
                    and self.server is None
                    and self._closing_generation is None
                ):
                    launch_cycle = self._new_recovery_cycle_locked()
            if old_token is not None:
                self._scheduler.cancel(old_token)
        if launch_cycle is not None:
            self._launch_recovery_attempt(launch_cycle, attempt_number=1)

    def reconcile(self, failure: AdbServerOwnershipLossFailure) -> None:
        """Invalidate the current owner from terminal liveness evidence and reconcile intent."""

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
        """Stop supervising without terminating native ADB or resetting a healthy owner."""

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
                attempt_threads = tuple(self._attempt_threads)
                self._recovery_epoch += 1
        for token in subscriptions:
            self._bus.unsubscribe(token)
        if retry_token is not None:
            self._scheduler.cancel(retry_token)
        for thread in attempt_threads:
            if thread is not current_thread():
                thread.join()

    def _invalidate_owner_and_maybe_recover(
        self,
        failure: AdbServerOwnershipLossFailure,
    ) -> None:
        retired_server: AdbOwnedServer | None = None

        with self._mutation_lock:
            with self._lock:
                self._require_open()
                if not self._desired_running:
                    return
                server = self.server

            if server is not None:
                self._owner_manager.retire(server)
                with self._lock:
                    if self.server is server:
                        self.server = None
                        self._closing_generation = server.generation
                        retired_server = server
                        self._recovery_epoch += 1

        if retired_server is None:
            return

        # Public ownership disappears before native close begins. Dependents use the neutral
        # retirement fact for teardown; loss remains separate failure evidence.
        try:
            self._bus.publish(
                AdbServerOwnershipRetired(
                    retired_server.endpoint,
                    retired_server.generation,
                )
            )
            self._bus.publish(
                AdbServerOwnershipLost(
                    retired_server.endpoint,
                    retired_server.generation,
                    failure,
                )
            )
        finally:
            self._launch_retired_disposal(retired_server)

    def _launch_retired_disposal(self, server: AdbOwnedServer) -> None:
        thread = self._thread_factory(
            target=self._run_retired_disposal,
            args=(server,),
            name=(
                "adb-owned-server-close-"
                f"{server.endpoint.host}-{server.endpoint.port}-{server.generation}"
            ),
        )
        with self._lock:
            if self._closed and self._closing_generation != server.generation:
                return
            self._attempt_threads.add(thread)
            try:
                thread.start()
            except BaseException:
                self._attempt_threads.discard(thread)
                raise

    def _run_retired_disposal(self, server: AdbOwnedServer) -> None:
        active = current_thread()
        try:
            try:
                self._owner_manager.dispose_retired(server)
            except Exception as exc:
                self._bus.publish(
                    AdbServerNativeCloseUnproven(
                        server.endpoint,
                        server.generation,
                        AdbServerCloseUnprovenFailure(str(exc)),
                    )
                )
                return

            try:
                self._bus.publish(
                    AdbServerNativeCloseCompleted(
                        server.endpoint,
                        server.generation,
                    )
                )
            finally:
                launch_cycle: AdbServerRecoveryCycleId | None = None
                with self._mutation_lock:
                    with self._lock:
                        if self._closing_generation == server.generation:
                            self._closing_generation = None
                        if (
                            not self._closed
                            and self._desired_running
                            and self._recovery_enabled
                            and self.server is None
                            and self._closing_generation is None
                            and self._cycle_id is None
                        ):
                            launch_cycle = self._new_recovery_cycle_locked()
                if launch_cycle is not None:
                    self._launch_recovery_attempt(launch_cycle, attempt_number=1)
        finally:
            with self._lock:
                self._attempt_threads.discard(active)

    def _launch_recovery_attempt(
        self,
        cycle_id: AdbServerRecoveryCycleId,
        attempt_number: int,
    ) -> None:
        thread = self._thread_factory(
            target=self._run_recovery_attempt,
            args=(cycle_id, attempt_number),
            name=(
                "adb-owned-server-recovery-"
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
                    recovered = self._owner_manager.acquire()
                except AdbServerLaunchError as exc:
                    launch_failure = AdbServerLaunchFailure(str(exc))
                else:
                    if recovered.endpoint != self.endpoint:
                        raise ValueError("owned-server recovery changed endpoint")
                    with self._lock:
                        if not self._recovery_is_current_locked(cycle_id):
                            return
                        retry_token = self._retry_token
                        self._retry_token = None
                        self._cycle_id = None
                        self.server = recovered
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
            if server is None or server.generation != event.generation:
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
        self._recovery_epoch += 1
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
            and self._closing_generation is None
            and self._cycle_id == cycle_id
        )

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


__all__ = ["AdbServerSupervisor"]
