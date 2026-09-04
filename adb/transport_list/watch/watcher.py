from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from adb.errors import AdbProtocolError, AdbServerConnectionError, AdbServiceError
from networking import TcpAddress
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.error import (
    AdbTransportListWatchCancelledError,
    AdbTransportListWatchError,
)
from adb.transport_list.watch.failure import (
    AdbTransportListWatchProtocolFailure,
    AdbTransportListWatchServerConnectionFailure,
    AdbTransportListWatchServiceFailure,
)
from adb.transport_list.watch.session import AdbTransportListWatchSession


@runtime_checkable
class AdbTransportListWatcher(Protocol):
    """Own one low-level transport-list watch attachment for an ADB server endpoint."""

    @property
    def address(self) -> TcpAddress:
        ...

    def open(self) -> AdbTransportListWatchSession | None:
        """Establish one watch session and synchronously obtain its initial complete list."""
        ...

    def close(self) -> None:
        """Release the watcher attachment and interrupt any active watch read."""
        ...


class _FailureNormalizingAdbTransportListWatchSession:
    """Translate raw ADB request errors into transport-list watch domain errors."""

    def __init__(self, session: AdbTransportListWatchSession) -> None:
        if not isinstance(session, AdbTransportListWatchSession):
            raise TypeError("session must satisfy AdbTransportListWatchSession")
        self._session = session

    @property
    def initial(self) -> AdbTransportList:
        try:
            return self._session.initial
        except BaseException as exc:
            _raise_normalized_watch_error(exc)
            raise AssertionError("unreachable")

    def updates(self) -> Iterator[AdbTransportList]:
        try:
            yield from self._session.updates()
        except BaseException as exc:
            _raise_normalized_watch_error(exc)
            raise AssertionError("unreachable")

    def close(self) -> None:
        self._session.close()


def open_transport_list_watch(
    watcher: AdbTransportListWatcher,
) -> AdbTransportListWatchSession:
    """Open one watcher through the domain error boundary.

    Concrete watchers may surface low-level ADB request exceptions or use ``None`` to report that
    closure interrupted startup. This boundary translates those implementation details into the
    stable watch-domain exceptions consumed by controllers and supervisors.
    """

    if not isinstance(watcher, AdbTransportListWatcher):
        raise TypeError("watcher must satisfy AdbTransportListWatcher")

    try:
        session = watcher.open()
    except BaseException as exc:
        _raise_normalized_watch_error(exc)
        raise AssertionError("unreachable")

    if session is None:
        raise AdbTransportListWatchCancelledError(
            "ADB transport-list watcher was closed before its initial transport list was established"
        )
    if not isinstance(session, AdbTransportListWatchSession):
        raise TypeError("transport-list watcher must return AdbTransportListWatchSession or None")
    return _FailureNormalizingAdbTransportListWatchSession(session)


def _raise_normalized_watch_error(exc: BaseException) -> None:
    if isinstance(exc, AdbTransportListWatchError):
        raise exc
    if isinstance(exc, AdbTransportListWatchCancelledError):
        raise exc
    if isinstance(exc, AdbServerConnectionError):
        raise AdbTransportListWatchError(
            AdbTransportListWatchServerConnectionFailure(str(exc) or None)
        ) from exc
    if isinstance(exc, AdbServiceError):
        raise AdbTransportListWatchError(
            AdbTransportListWatchServiceFailure(str(exc) or None)
        ) from exc
    if isinstance(exc, AdbProtocolError):
        raise AdbTransportListWatchError(
            AdbTransportListWatchProtocolFailure(str(exc) or None)
        ) from exc
    raise exc


__all__ = [
    "AdbTransportListWatcher",
    "open_transport_list_watch",
]
