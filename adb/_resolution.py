from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import socket
from threading import Event, Lock, Thread

from adb.errors import AdbTimeoutError


class AddressResolutionCancelled(RuntimeError):
    """The caller stopped waiting for address resolution."""


@dataclass(slots=True)
class _Resolution:
    key: tuple[str, int]
    done: Event = field(default_factory=Event)
    addresses: list[tuple] | None = None
    error: BaseException | None = None


class DeadlineResolver:
    """Bound the wait for DNS without abandoning resource-producing acquisition work.

    Only the resolver runs in a daemon thread; socket/process creation remains with the caller.
    At most one unresolved lookup is retained per instance, including after cancellation/timeout.
    Retries share that lookup or wait for its slot, rather than accumulating stuck DNS threads.
    The underlying resolver itself cannot be forcibly stopped. A completed lookup is not cached.
    """

    def __init__(
        self, resolver: Callable[..., list[tuple]], clock: Callable[[], float]
    ) -> None:
        self._resolver = resolver
        self._clock = clock
        self._lock = Lock()
        self._pending: _Resolution | None = None

    def _remaining(self, deadline: float, cancellation: Event | None) -> float:
        if cancellation is not None and cancellation.is_set():
            raise AddressResolutionCancelled
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise AdbTimeoutError("ADB address resolution timed out")
        return remaining

    def resolve(
        self, host: str, port: int, *, deadline: float, cancellation: Event | None
    ) -> list[tuple]:
        key = (host, port)
        while True:
            self._remaining(deadline, cancellation)
            with self._lock:
                job = self._pending
                start = job is None
                if job is None:
                    job = _Resolution(key)
                    self._pending = job
            if start:
                worker: Thread | None = None
                try:
                    worker = Thread(
                        target=self._resolve, args=(job,), name="adb-address-resolution", daemon=True
                    )
                    worker.start()
                except BaseException as exc:
                    # A start that was interrupted after launching must keep its slot. Otherwise
                    # retries could accumulate unresolved workers and consume a fabricated result.
                    if worker is None or (isinstance(exc, Exception) and worker.ident is None):
                        job.error = exc
                        job.done.set()
                        with self._lock:
                            if self._pending is job:
                                self._pending = None
                    raise

            while not job.done.is_set():
                remaining = self._remaining(deadline, cancellation)
                job.done.wait(min(0.05, remaining))
            self._remaining(deadline, cancellation)
            with self._lock:
                if self._pending is job:
                    self._pending = None
            if job.key != key:
                continue
            if job.error is not None:
                raise job.error
            assert job.addresses is not None
            return job.addresses

    def _resolve(self, job: _Resolution) -> None:
        try:
            job.addresses = self._resolver(*job.key, type=socket.SOCK_STREAM)
        except BaseException as exc:
            job.error = exc
        finally:
            job.done.set()


__all__ = ["AddressResolutionCancelled", "DeadlineResolver"]
