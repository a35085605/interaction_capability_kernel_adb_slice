from __future__ import annotations

from collections.abc import Iterable

from adb.runtime.server import (
    AdbServerActivateFailed,
    AdbServerAvailabilityConflict,
    AdbServerAvailabilityFailed,
    AdbServerAvailabilityPolicy,
    AdbServerAvailabilitySupervisor,
    AdbServerAvailable,
    bootstrap_adb_server_runtime,
)
from adb.server.coordinator import AdbServerLifecycleCoordinator
from adb.server.request import AdbServerRequest
from adb.server.snapshot import AdbServerPhase
from lifecycle.resource.result import ResourceAcquireFailed, ResourceAcquireSucceeded
from networking import TcpEndpoint


class _SequencedServerProvider:
    def __init__(self, outcomes: Iterable[BaseException | None]) -> None:
        self._outcomes = iter(outcomes)
        self.acquired: list[str] = []
        self.released: list[tuple[str, ...]] = []

    def acquire(self, request: AdbServerRequest):
        resource = f"server:{len(self.acquired) + 1}"
        self.acquired.append(resource)
        outcome = next(self._outcomes)
        resources = (resource,)
        if outcome is not None:
            return ResourceAcquireFailed(outcome, resources)
        return ResourceAcquireSucceeded(resources)

    def release(self, resources: tuple[str, ...]) -> None:
        self.released.append(resources)


def _runtime(provider: _SequencedServerProvider):
    return bootstrap_adb_server_runtime(
        lambda issuer: AdbServerLifecycleCoordinator(issuer, provider)
    )


def _request(port: int = 5037) -> AdbServerRequest:
    return AdbServerRequest(TcpEndpoint("127.0.0.1", port))


def _policy(*, max_attempts: int | None = None) -> AdbServerAvailabilityPolicy:
    return AdbServerAvailabilityPolicy(
        retry_initial_seconds=0.1,
        retry_max_seconds=1.0,
        retry_multiplier=2.0,
        retry_jitter_ratio=0.0,
        deferred_retry_seconds=0.05,
        max_attempts=max_attempts,
    )


def test_availability_supervisor_releases_failed_generations_until_active() -> None:
    first = RuntimeError("first")
    second = RuntimeError("second")
    provider = _SequencedServerProvider((first, second, None))
    runtime = _runtime(provider)
    sleeps: list[float] = []
    supervisor = AdbServerAvailabilitySupervisor(
        runtime,
        policy=_policy(),
        _sleeper=sleeps.append,
        _random=lambda: 0.5,
    )

    result = supervisor.supervise(_request())

    assert isinstance(result, AdbServerAvailable)
    assert result.snapshot.phase is AdbServerPhase.ACTIVE
    assert result.snapshot.capability is not None
    assert result.attempts == 3
    assert result.failed_attempts == 2
    assert provider.acquired == ["server:1", "server:2", "server:3"]
    assert provider.released == [("server:1",), ("server:2",)]
    assert sleeps == [0.1, 0.2]


def test_availability_supervisor_exhausts_only_after_failed_generation_is_released() -> None:
    first = RuntimeError("first")
    second = RuntimeError("second")
    provider = _SequencedServerProvider((first, second))
    runtime = _runtime(provider)
    supervisor = AdbServerAvailabilitySupervisor(
        runtime,
        policy=_policy(max_attempts=2),
        _sleeper=lambda _: None,
        _random=lambda: 0.5,
    )

    result = supervisor.supervise(_request())

    assert isinstance(result, AdbServerAvailabilityFailed)
    assert result.failed_attempts == 2
    assert result.cause is second
    assert result.snapshot.phase is AdbServerPhase.IDLE
    assert runtime.snapshot.read().phase is AdbServerPhase.IDLE
    assert provider.released == [("server:1",), ("server:2",)]


def test_existing_release_required_debt_is_cleaned_without_consuming_retry_budget() -> None:
    old_failure = RuntimeError("old failure")
    provider = _SequencedServerProvider((old_failure, None))
    runtime = _runtime(provider)
    request = _request()

    failed = runtime.mutations.activate(request)
    assert isinstance(failed, AdbServerActivateFailed)
    assert runtime.snapshot.read().phase is AdbServerPhase.RELEASE_REQUIRED

    supervisor = AdbServerAvailabilitySupervisor(
        runtime,
        policy=_policy(max_attempts=1),
        _sleeper=lambda _: None,
        _random=lambda: 0.5,
    )
    result = supervisor.supervise(request)

    assert isinstance(result, AdbServerAvailable)
    assert result.attempts == 1
    assert result.failed_attempts == 0
    assert provider.released == [("server:1",)]


def test_availability_supervisor_does_not_replace_conflicting_active_request() -> None:
    provider = _SequencedServerProvider((None,))
    runtime = _runtime(provider)
    current_request = _request(5037)
    requested = _request(6037)
    runtime.mutations.activate(current_request)
    supervisor = AdbServerAvailabilitySupervisor(runtime, policy=_policy())

    result = supervisor.supervise(requested)

    assert isinstance(result, AdbServerAvailabilityConflict)
    assert result.current_request == current_request
    assert runtime.snapshot.read().request == current_request
    assert provider.released == []


def test_availability_supervisor_returns_already_available_without_new_attempt() -> None:
    provider = _SequencedServerProvider((None,))
    runtime = _runtime(provider)
    request = _request()
    runtime.mutations.activate(request)
    supervisor = AdbServerAvailabilitySupervisor(runtime, policy=_policy())

    result = supervisor.ensure_available(request)

    assert isinstance(result, AdbServerAvailable)
    assert result.attempts == 0
    assert result.failed_attempts == 0
    assert provider.acquired == ["server:1"]
