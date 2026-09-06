from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from adb.server.identity import AdbServerIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import (
    AdbTransportListObservation,
    AdbTransportListObservationIdentifier,
)
from adb.transport_list.watch.stream import AdbTransportListWatchStream


@runtime_checkable
class AdbTransportListWatchSession(Protocol):
    """Server-bound identified watch session for one authoritative ADB server lifetime."""

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
    """Bind one raw watch stream to runtime-issued observation identities and server provenance."""

    __slots__ = ("_server", "_stream", "_initial", "_observation_identifier")

    def __init__(
        self,
        server: AdbServerIdentity,
        stream: AdbTransportListWatchStream,
        initial: AdbTransportList,
        observation_identifier: AdbTransportListObservationIdentifier,
    ) -> None:
        if not isinstance(server, AdbServerIdentity):
            raise TypeError("server must be AdbServerIdentity")
        if not isinstance(stream, AdbTransportListWatchStream):
            raise TypeError("stream must satisfy AdbTransportListWatchStream")
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
        self._observation_identifier = observation_identifier
        self._initial = observation_identifier.identify(server, initial)

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
        self._stream.close()


def bind_transport_list_watch_session(
    server: AdbServerIdentity,
    stream: AdbTransportListWatchStream,
    initial: AdbTransportList,
    observation_identifier: AdbTransportListObservationIdentifier,
) -> AdbTransportListWatchSession:
    """Bind a raw stream to one runtime/server observation boundary."""

    return _ServerBoundAdbTransportListWatchSession(
        server,
        stream,
        initial,
        observation_identifier,
    )


__all__ = [
    "AdbTransportListWatchSession",
    "AdbTransportListWatchStream",
    "bind_transport_list_watch_session",
]
