from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from threading import Condition, Event, Lock, Thread, current_thread
from typing import Protocol, TypeAlias, runtime_checkable

from adb.errors import AdbProtocolError, AdbServerConnectionError, AdbServiceError
from networking import TcpAddress
from adb.transport_list.coordinator import AdbTransportListCoordinator
from adb.transport_list.session_identity import AdbTransportListSessionIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.state import (
    AdbTransportListObservationStateConflict,
    AdbTransportListObserved,
)
from adb.transport_list.watch.attachment import AdbTransportListWatchAttachment
from adb.transport_list.watch.session import (
    AdbTransportListWatchSession,
    bind_transport_list_watch_session,
)
from adb.transport_list.watch.stream import AdbTransportListWatchStream
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
from adb.transport_list.watch.signal import (
    AdbTransportListWatchFailed,
    AdbTransportListWatchStarted,
    AdbTransportListWatchStopped,
)
from eventing import EventPublisher


_TransportListWatchAttachmentFactory = Callable[
    [TcpAddress, float], AdbTransportListWatchAttachment
]
_ThreadFactory = Callable[..., Thread]


def _default_thread_factory(*args, **kwargs) -> Thread:
    thread = Thread(*args, **kwargs)
    thread.daemon = True
    return thread


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStartSucceeded:
    """The watch entered stream mode and committed its initial complete transport list."""

    session_identity: AdbTransportListSessionIdentity
    initial: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.session_identity, AdbTransportListSessionIdentity):
            raise TypeError("session_identity must be AdbTransportListSessionIdentity")
        if not isinstance(self.initial, AdbTransportList):
            raise TypeError("initial must be AdbTransportList")


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStartCancelled:
    """The attachment reported that startup did not establish a watch stream."""

    session_identity: AdbTransportListSessionIdentity | None = None

    def __post_init__(self) -> None:
        if self.session_identity is not None and not isinstance(
            self.session_identity, AdbTransportListSessionIdentity
        ):
            raise TypeError(
                "session_identity must be AdbTransportListSessionIdentity or None"
            )


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStartSuperseded:
    """Startup lost its endpoint/session authority fence before becoming active."""

    session_identity: AdbTransportListSessionIdentity | None = None

    def __post_init__(self) -> None:
        if self.session_identity is not None and not isinstance(
            self.session_identity, AdbTransportListSessionIdentity
        ):
            raise TypeError(
                "session_identity must be AdbTransportListSessionIdentity or None"
            )


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchStartFailed:
    """Startup completed with a known transport-list watch failure."""

    session_identity: AdbTransportListSessionIdentity | None
    failure: AdbTransportListWatchFailure

    def __post_init__(self) -> None:
        if self.session_identity is not None and not isinstance(
            self.session_identity, AdbTransportListSessionIdentity
        ):
            raise TypeError(
                "session_identity must be AdbTransportListSessionIdentity or None"
            )
        if not isinstance(self.failure, AdbTransportListWatchFailure):
            raise TypeError("failure must be AdbTransportListWatchFailure")


AdbTransportListWatchStartResult: TypeAlias = (
    AdbTransportListWatchStartSucceeded
    | AdbTransportListWatchStartCancelled
    | AdbTransportListWatchStartSuperseded
    | AdbTransportListWatchStartFailed
)


@dataclass(frozen=True, slots=True)
class _AdbTransportListWatchOpened:
    stream: AdbTransportListWatchStream
    initial: AdbTransportList


@dataclass(frozen=True, slots=True)
class _AdbTransportListWatchOpenCancelled:
    pass



@runtime_checkable
class AdbTransportListWatchController(Protocol):
    """Controller bound to one endpoint and owning short-lived watch resources."""

    @property
    def endpoint(self) -> TcpAddress:
        """Immutable attachment endpoint owned by this controller."""
        ...

    @property
    def active(self) -> bool:
        ...

    def start(self) -> AdbTransportListWatchStartResult:
        """Establish one fresh raw watch session for this endpoint."""
        ...

    def stop(self) -> None:
        """Stop the current session while preserving this controller's endpoint."""
        ...

    def close(self) -> None:
        """Permanently close the controller and its current watch resources."""
        ...

__all__ = [
    "AdbTransportListWatchController",
    "AdbTransportListWatchStartCancelled",
    "AdbTransportListWatchStartFailed",
    "AdbTransportListWatchStartResult",
    "AdbTransportListWatchStartSucceeded",
    "AdbTransportListWatchStartSuperseded",
    "ThreadedAdbTransportListWatchController",
]
