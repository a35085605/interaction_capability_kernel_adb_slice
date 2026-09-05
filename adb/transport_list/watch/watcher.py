from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

from adb.errors import AdbProtocolError, AdbServerConnectionError, AdbServiceError
from networking import TcpAddress
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.error import (
    AdbTransportListWatchCancelledError,
    AdbTransportListWatchError,
)
from adb.transport_list.watch.failure import (
    AdbTransportListWatchFailure,
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
        """Attempt to establish one watch session and synchronously obtain its
        initial complete list.

        Return ``None`` when watcher closure cancels startup.
        """
        ...

    def close(self) -> None:
        """Release the watcher attachment and interrupt any active watch read."""
        ...


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchOpened:
    """A watch session and its initial complete transport list were established."""

    session: AdbTransportListWatchSession
    initial: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListWatchSession):
            raise TypeError("session must satisfy AdbTransportListWatchSession")
        if not isinstance(self.initial, AdbTransportList):
            raise TypeError("initial must be AdbTransportList")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchOpenCancelled:
    """Opening was interrupted by watcher closure before an initial list was established."""


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchOpenFailed:
    """Opening completed with a known transport-list watch failure."""

    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


AdbTransportListWatchOpenResult: TypeAlias = (
    AdbTransportListWatchOpened
    | AdbTransportListWatchOpenCancelled
    | AdbTransportListWatchOpenFailed
)


class _FailureNormalizingAdbTransportListWatchSession:
    """Translate ADB request errors from an established stream into typed watch errors."""

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
) -> AdbTransportListWatchOpenResult:
    """Open a watcher and normalize expected startup outcomes.

    Returns typed cancellation or failure evidence for known startup conditions while
    preserving unexpected exceptions.
    """

    if not isinstance(watcher, AdbTransportListWatcher):
        raise TypeError("watcher must satisfy AdbTransportListWatcher")

    try:
        session = watcher.open()
    except AdbTransportListWatchCancelledError:
        return AdbTransportListWatchOpenCancelled()
    except BaseException as exc:
        failure = _watch_failure_from_exception(exc)
        if failure is None:
            raise
        return AdbTransportListWatchOpenFailed(failure)

    if session is None:
        return AdbTransportListWatchOpenCancelled()
    if not isinstance(session, AdbTransportListWatchSession):
        raise TypeError("transport-list watcher must return AdbTransportListWatchSession or None")

    normalized_session = _FailureNormalizingAdbTransportListWatchSession(session)
    try:
        initial = normalized_session.initial
    except AdbTransportListWatchCancelledError:
        normalized_session.close()
        return AdbTransportListWatchOpenCancelled()
    except AdbTransportListWatchError as exc:
        normalized_session.close()
        return AdbTransportListWatchOpenFailed(exc.failure)
    except BaseException:
        normalized_session.close()
        raise

    if not isinstance(initial, AdbTransportList):
        normalized_session.close()
        raise TypeError("transport-list watch session initial must be AdbTransportList")
    return AdbTransportListWatchOpened(normalized_session, initial)


def _watch_failure_from_exception(
    exc: BaseException,
) -> AdbTransportListWatchFailure | None:
    if isinstance(exc, AdbTransportListWatchError):
        return exc.failure
    if isinstance(exc, AdbServerConnectionError):
        return AdbTransportListWatchServerConnectionFailure(str(exc) or None)
    if isinstance(exc, AdbServiceError):
        return AdbTransportListWatchServiceFailure(str(exc) or None)
    if isinstance(exc, AdbProtocolError):
        return AdbTransportListWatchProtocolFailure(str(exc) or None)
    return None


def _raise_normalized_watch_error(exc: BaseException) -> None:
    if isinstance(exc, AdbTransportListWatchCancelledError):
        raise exc
    failure = _watch_failure_from_exception(exc)
    if failure is not None:
        if isinstance(exc, AdbTransportListWatchError):
            raise exc
        raise AdbTransportListWatchError(failure) from exc
    raise exc


__all__ = [
    "AdbTransportListWatchOpenCancelled",
    "AdbTransportListWatchOpenFailed",
    "AdbTransportListWatchOpened",
    "AdbTransportListWatchOpenResult",
    "AdbTransportListWatcher",
    "open_transport_list_watch",
]
