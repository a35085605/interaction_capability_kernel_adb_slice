from __future__ import annotations

from collections.abc import Iterator
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.transport_list.coordinator import AdbTransportListCoordinator
from adb.transport_list.session_identity import AdbTransportListSessionIdentity
from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservationBasis
from adb.transport_list.watch.attachment import AdbTransportListWatchAttachment
from adb.transport_list.watch.stream import AdbTransportListWatchStream


@runtime_checkable
class AdbTransportListWatchSession(Protocol):
    """Short-lived raw watch session owning one observation-authority session identity."""

    @property
    def session_identity(self) -> AdbTransportListSessionIdentity:
        ...

    @property
    def initial_basis(self) -> AdbTransportListObservationBasis:
        ...

    @property
    def initial(self) -> AdbTransportList:
        ...

    def updates(self) -> Iterator[AdbTransportList]:
        ...

    def close(self) -> None:
        ...


class _SessionIdentityBoundAdbTransportListWatchSession:
    """Own one producer identity together with its raw watch resources."""

    __slots__ = (
        "_initial_basis",
        "_coordinator",
        "_stream",
        "_attachment",
        "_initial",
        "_close_lock",
        "_closed",
    )

    def __init__(
        self,
        initial_basis: AdbTransportListObservationBasis,
        coordinator: AdbTransportListCoordinator,
        stream: AdbTransportListWatchStream,
        initial: AdbTransportList,
        *,
        attachment: AdbTransportListWatchAttachment | None = None,
    ) -> None:
        if not isinstance(initial_basis, AdbTransportListObservationBasis):
            raise TypeError("initial_basis must be AdbTransportListObservationBasis")
        if not isinstance(coordinator, AdbTransportListCoordinator):
            raise TypeError("coordinator must be AdbTransportListCoordinator")
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
        self._initial_basis = initial_basis
        self._coordinator = coordinator
        self._stream = stream
        self._attachment = attachment
        self._initial = initial
        self._close_lock = Lock()
        self._closed = False

    @property
    def session_identity(self) -> AdbTransportListSessionIdentity:
        return self._initial_basis.session_identity

    @property
    def initial_basis(self) -> AdbTransportListObservationBasis:
        return self._initial_basis

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

        # Revoke producer authority before potentially blocking resource cleanup. A stale close
        # cannot invalidate a replacement session because revocation is session-identity-fenced.
        self._coordinator.revoke(self.session_identity)

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
    coordinator: AdbTransportListCoordinator,
    stream: AdbTransportListWatchStream,
    initial: AdbTransportList,
    *,
    attachment: AdbTransportListWatchAttachment | None = None,
) -> AdbTransportListWatchSession | None:
    """Acquire producer authority and bind it to established raw watch resources."""

    if not isinstance(coordinator, AdbTransportListCoordinator):
        raise TypeError("coordinator must be AdbTransportListCoordinator")
    if not isinstance(stream, AdbTransportListWatchStream):
        raise TypeError("stream must satisfy AdbTransportListWatchStream")
    if attachment is not None and not isinstance(
        attachment, AdbTransportListWatchAttachment
    ):
        raise TypeError("attachment must satisfy AdbTransportListWatchAttachment or be None")
    if not isinstance(initial, AdbTransportList):
        raise TypeError("initial must be AdbTransportList")

    initial_basis = coordinator.begin()
    if initial_basis is None:
        return None

    try:
        return _SessionIdentityBoundAdbTransportListWatchSession(
            initial_basis,
            coordinator,
            stream,
            initial,
            attachment=attachment,
        )
    except BaseException:
        coordinator.revoke(initial_basis.session_identity)
        raise


__all__ = [
    "AdbTransportListWatchSession",
    "AdbTransportListWatchStream",
    "bind_transport_list_watch_session",
]
