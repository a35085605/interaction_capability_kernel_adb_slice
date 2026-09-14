from __future__ import annotations

import socket

import pytest

from _lifecycle_new.resource.driver import RequirementAcquireInterrupted
from adb.aosp.io.server_process import AospAdbServerProcessDriver
from adb.aosp.io.track_devices import AospTrackDevicesSessionDriver
from networking import TcpEndpoint


class _ServerSocket:
    def __init__(self) -> None:
        self.closed = False

    def setsockopt(self, *args: object) -> None:
        pass

    def bind(self, sockaddr: object) -> None:
        pass

    def getsockname(self) -> tuple[str, int]:
        return ("127.0.0.1", 5037)

    def listen(self, backlog: int) -> None:
        pass

    def fileno(self) -> int:
        return 7

    def close(self) -> None:
        self.closed = True


class _TrackSocket:
    def __init__(self, interruption: BaseException) -> None:
        self._interruption = interruption
        self.close_calls = 0
        self.closed = False

    def settimeout(self, timeout: float | None) -> None:
        pass

    def connect(self, sockaddr: object) -> None:
        pass

    def sendall(self, payload: bytes) -> None:
        raise self._interruption

    def recv(self, size: int) -> bytes:
        raise AssertionError("recv should not be reached")

    def shutdown(self, how: int) -> None:
        pass

    def close(self) -> None:
        self.close_calls += 1
        if self.close_calls == 1:
            raise OSError("close not confirmed")
        self.closed = True


def _resolver(host: str, port: int, **kwargs: object) -> list[tuple]:
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (host, port),
        )
    ]


def test_server_process_reports_interruption_with_owned_listener() -> None:
    interruption = KeyboardInterrupt()
    listener = _ServerSocket()

    def popen(*args: object, **kwargs: object) -> object:
        raise interruption

    driver = AospAdbServerProcessDriver(
        resolver=_resolver,
        socket_factory=lambda *args: listener,
        popen_factory=popen,
        monotonic_clock=lambda: 0.0,
        socket_activation_supported=True,
    )

    result = driver.acquire(TcpEndpoint("127.0.0.1", 5037))

    assert isinstance(result, RequirementAcquireInterrupted)
    assert result.error is interruption
    assert result.resources == (listener,)
    assert not listener.closed

    driver.cleanup(result.resources)
    assert listener.closed


def test_track_devices_reports_interruption_when_session_cleanup_is_unconfirmed() -> None:
    interruption = KeyboardInterrupt()
    sock = _TrackSocket(interruption)
    driver = AospTrackDevicesSessionDriver(
        _resolver=_resolver,
        _socket_factory=lambda *args: sock,
        _clock=lambda: 0.0,
    )

    result = driver.acquire(TcpEndpoint("127.0.0.1", 5037))

    assert isinstance(result, RequirementAcquireInterrupted)
    assert result.error is interruption
    assert len(result.resources) == 1
    assert not sock.closed

    driver.cleanup(result.resources)
    assert sock.closed


def test_interruption_is_recorded_until_lifecycle_release_completes() -> None:
    from _lifecycle_new.capability.coordinator import CapabilityLifecycleCoordinator
    from _lifecycle_new.capability.result import ReleaseSucceeded
    from _lifecycle_new.capability.snapshot import LifecyclePhase
    from _lifecycle_new.resource.manager import ResolvedResourceProvider

    interruption = KeyboardInterrupt()
    listener = _ServerSocket()

    def popen(*args: object, **kwargs: object) -> object:
        raise interruption

    driver = AospAdbServerProcessDriver(
        resolver=_resolver,
        socket_factory=lambda *args: listener,
        popen_factory=popen,
        monotonic_clock=lambda: 0.0,
        socket_activation_supported=True,
    )

    class Resolver:
        def resolve(self, request: TcpEndpoint) -> tuple[TcpEndpoint, ...]:
            return (request,)

    class Projector:
        def project(self, request: TcpEndpoint, resources: tuple[object, ...]) -> object:
            return object()

    generation = 0

    def issue_generation() -> int:
        nonlocal generation
        generation += 1
        return generation

    lifecycle = CapabilityLifecycleCoordinator(
        issue_generation,
        ResolvedResourceProvider(Resolver(), driver),
        Projector(),
    )
    request = TcpEndpoint("127.0.0.1", 5037)

    with pytest.raises(KeyboardInterrupt) as caught:
        lifecycle.acquire(1, request)

    assert caught.value is interruption
    snapshot = lifecycle.read()
    assert snapshot.phase is LifecyclePhase.RELEASE_REQUIRED
    assert snapshot.last_error is interruption
    assert not listener.closed

    released = lifecycle.release(1, request)
    assert isinstance(released, ReleaseSucceeded)
    assert released.next_generation == 2
    assert listener.closed
