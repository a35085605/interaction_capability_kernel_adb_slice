from __future__ import annotations

from collections.abc import Iterator
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import (
    AdbTransportListObservation,
    AdbTransportListObservationIdentifier,
)
from adb.transport_list.watch.attachment import AdbTransportListWatchAttachment
from adb.transport_list.watch.stream import AdbTransportListWatchStream


@runtime_checkable
class AdbTransportListWatchSession(Protocol):
    """Short-lived server-bound watch session for one authoritative ADB server lifetime."""

    @property
    def server(self) -> AdbServerIdentity:
        ...

    @property
    def initial(self) -> AdbTransportListObservation:
        ...

    def updates(self) -> Iterator[AdbTransportListObservation]:
        ...

    def close(self) -> None:
        ...


class _ServerBoundAdbTransportListWatchSession:
    """Bind one short-lived raw stream and attachment to server-scoped observations."""

    __slots__ = (
        "_server",
        "_stream",
        "_attachment",
        "_initial",
        "_observation_identifier",
        "_close_lock",
        "_closed",
    )

    def __init__(
        self,
        server: AdbServerIdentity,
        stream: AdbTransportListWatchStream,
        initial: AdbTransportList,
        observation_identifier: AdbTransportListObservationIdentifier,
        *,
        attachment: AdbTransportListWatchAttachment | None = None,
    ) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(stream, AdbTransportListWatchStream):
            raise TypeError("stream must satisfy AdbTransportListWatchStream")
        if attachment is not None and not isinstance(
            attachment, AdbTransportListWatchAttachment
        ):
            raise TypeError(
                "attachment must satisfy AdbTransportListWatchAttachment or be None"
            )
        if not isinstance(initial, AdbTransportList):
            raise TypeError("initial must be AdbTransportList")
        if not isinstance(
            observation_identifier, AdbTransportListObservationIdentifier
        ):
            raise TypeError(
                "observation_identifier must be AdbTransportListObservationIdentifier"
            )
        self._server = server
        self._stream = stream
        self._attachment = attachment
        self._observation_identifier = observation_identifier
        self._initial = observation_identifier.identify(server, initial)
        self._close_lock = Lock()
        self._closed = False

    @property
    def server(self) -> AdbServerIdentity:
        return self._server

    @property
    def initial(self) -> AdbTransportListObservation:
        return self._initial

    def updates(self) -> Iterator[AdbTransportListObservation]:
        for transport_list in self._stream.updates():
            if not isinstance(transport_list, AdbTransportList):
                raise TypeError("transport-list watch stream must yield AdbTransportList")
            yield self._observation_identifier.identify(self._server, transport_list)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        first_error: BaseException | None = None
        try:
            self._stream.close()
        except BaseException as exc:
            first_error = exc
        if self._attachment is not None:
            try:
                self._attachment.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc

        if first_error is not None:
            raise first_error


def bind_transport_list_watch_session(
    server: AdbServerIdentity,
    stream: AdbTransportListWatchStream,
    initial: AdbTransportList,
    observation_identifier: AdbTransportListObservationIdentifier,
    *,
    attachment: AdbTransportListWatchAttachment | None = None,
) -> AdbTransportListWatchSession:
    """Bind one raw stream to a runtime/server observation boundary.

    ``attachment`` transfers low-level resource ownership to the session when supplied.
    """

    return _ServerBoundAdbTransportListWatchSession(
        server,
        stream,
        initial,
        observation_identifier,
        attachment=attachment,
    )


__all__ = [
    "AdbTransportListWatchSession",
    "AdbTransportListWatchStream",
    "bind_transport_list_watch_session",
]
