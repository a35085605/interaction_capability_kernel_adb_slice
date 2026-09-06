from __future__ import annotations

from collections.abc import Iterator
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.watch.attachment import AdbTransportListWatchAttachment
from adb.transport_list.watch.stream import AdbTransportListWatchStream


@runtime_checkable
class AdbTransportListWatchSession(Protocol):
    """Short-lived server-bound raw watch session for one ADB server lifetime."""

    @property
    def server(self) -> AdbServerIdentity:
        ...

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...

    def close(self) -> None:
        ...


class _ServerBoundAdbTransportListWatchSession:
    """Own one short-lived raw stream and attachment for a fixed server binding."""

    __slots__ = (
        "_server",
        "_stream",
        "_attachment",
        "_initial",
        "_close_lock",
        "_closed",
    )

    def __init__(
        self,
        server: AdbServerIdentity,
        stream: AdbTransportListWatchStream,
        initial: AdbTransportList,
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
        self._server = server
        self._stream = stream
        self._attachment = attachment
        self._initial = initial
        self._close_lock = Lock()
        self._closed = False

    @property
    def server(self) -> AdbServerIdentity:
        return self._server

    @property
    def initial(self) -> AdbTransportList:
        return self._initial

    def updates(self) -> Iterator[AdbTransportList]:
        for transport_list in self._stream.updates():
            if not isinstance(transport_list, AdbTransportList):
                raise TypeError("transport-list watch stream must yield AdbTransportList")
            yield transport_list

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
    *,
    attachment: AdbTransportListWatchAttachment | None = None,
) -> AdbTransportListWatchSession:
    """Bind one raw stream to a fixed server lifetime and transfer attachment ownership."""

    return _ServerBoundAdbTransportListWatchSession(
        server,
        stream,
        initial,
        attachment=attachment,
    )


__all__ = [
    "AdbTransportListWatchSession",
    "AdbTransportListWatchStream",
    "bind_transport_list_watch_session",
]
