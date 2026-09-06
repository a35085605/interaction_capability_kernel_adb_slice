from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from threading import Lock
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
from adb.transport_list.watch.stream import AdbTransportListWatchStream


@runtime_checkable
class AdbTransportListWatchAttachment(Protocol):
    """One low-level, endpoint-bound attachment used by exactly one watch session."""

    @property
    def address(self) -> TcpAddress:
        ...

    def open(self) -> AdbTransportListWatchStream | None:
        """Open the attachment and synchronously obtain its initial complete list."""
        ...

    def close(self) -> None:
        """Release the attachment and interrupt any active open/read operation."""
        ...


_TransportListWatchAttachmentFactory = Callable[
    [TcpAddress, float], AdbTransportListWatchAttachment
]


@runtime_checkable
class AdbTransportListWatcher(Protocol):
    """Long-lived capability that opens short-lived watch streams for server endpoints."""

    def open(self, address: TcpAddress) -> AdbTransportListWatchStream | None:
        """Open one short-lived raw watch stream for ``address``."""
        ...

    def cancel(self) -> None:
        """Cancel current attachment work without closing this reusable watcher."""
        ...

    def close(self) -> None:
        """Permanently close this watcher and every attachment it currently owns."""
        ...


class _AttachmentBackedAdbTransportListWatchStream:
    """Keep one low-level attachment alive exactly as long as its returned stream."""

    __slots__ = ("_stream", "_attachment", "_release", "_lock", "_closed")

    def __init__(
        self,
        stream: AdbTransportListWatchStream,
        attachment: AdbTransportListWatchAttachment,
        release: Callable[[AdbTransportListWatchAttachment], None],
    ) -> None:
        if not isinstance(stream, AdbTransportListWatchStream):
            raise TypeError("stream must satisfy AdbTransportListWatchStream")
        if not isinstance(attachment, AdbTransportListWatchAttachment):
            raise TypeError("attachment must satisfy AdbTransportListWatchAttachment")
        if not callable(release):
            raise TypeError("release must be callable")
        self._stream = stream
        self._attachment = attachment
        self._release = release
        self._lock = Lock()
        self._closed = False

    @property
    def initial(self) -> AdbTransportList:
        return self._stream.initial

    def updates(self) -> Iterator[AdbTransportList]:
        yield from self._stream.updates()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True

        first_error: BaseException | None = None
        try:
            self._stream.close()
        except BaseException as exc:
            first_error = exc
        try:
            self._attachment.close()
        except BaseException as exc:
            if first_error is None:
                first_error = exc
        finally:
            self._release(self._attachment)

        if first_error is not None:
            raise first_error


class ReusableAdbTransportListWatcher:
    """Long-lived watcher backed by one short-lived low-level attachment per open stream."""

    def __init__(
        self,
        attachment_factory: _TransportListWatchAttachmentFactory,
        *,
        startup_timeout_seconds: float,
    ) -> None:
        if not callable(attachment_factory):
            raise TypeError("attachment_factory must be callable")
        self._attachment_factory = attachment_factory
        self._startup_timeout_seconds = startup_timeout_seconds
        self._lock = Lock()
        self._active_attachments: dict[int, AdbTransportListWatchAttachment] = {}
        self._cancel_generation = 0
        self._closed = False

    def open(self, address: TcpAddress) -> AdbTransportListWatchStream | None:
        if not isinstance(address, TcpAddress):
            raise TypeError("address must be TcpAddress")

        with self._lock:
            if self._closed:
                raise AdbTransportListWatchCancelledError(
                    "transport-list watcher is closed"
                )
            generation = self._cancel_generation

        attachment = self._attachment_factory(
            address,
            self._startup_timeout_seconds,
        )
        if not isinstance(attachment, AdbTransportListWatchAttachment):
            raise TypeError(
                "transport-list attachment factory must return "
                "AdbTransportListWatchAttachment"
            )
        if attachment.address != address:
            attachment.close()
            raise ValueError(
                "transport-list attachment factory returned a mismatched server endpoint"
            )

        key = id(attachment)
        with self._lock:
            if self._closed or generation != self._cancel_generation:
                cancelled_before_open = True
            else:
                cancelled_before_open = False
                self._active_attachments[key] = attachment
        if cancelled_before_open:
            attachment.close()
            raise AdbTransportListWatchCancelledError(
                "transport-list watcher open was cancelled"
            )

        try:
            stream = attachment.open()
        except BaseException:
            self._release_attachment(attachment)
            try:
                attachment.close()
            except BaseException:
                # Preserve the open failure; cleanup failure must not replace its diagnosis.
                pass
            raise

        if stream is None:
            self._release_attachment(attachment)
            attachment.close()
            return None
        if not isinstance(stream, AdbTransportListWatchStream):
            self._release_attachment(attachment)
            attachment.close()
            raise TypeError(
                "transport-list watch attachment must return "
                "AdbTransportListWatchStream or None"
            )

        with self._lock:
            cancelled_after_open = (
                self._closed
                or generation != self._cancel_generation
                or self._active_attachments.get(key) is not attachment
            )
        if cancelled_after_open:
            first_error: BaseException | None = None
            try:
                stream.close()
            except BaseException as exc:
                first_error = exc
            try:
                attachment.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
            finally:
                self._release_attachment(attachment)
            if first_error is not None:
                raise first_error
            raise AdbTransportListWatchCancelledError(
                "transport-list watcher open was cancelled"
            )

        return _AttachmentBackedAdbTransportListWatchStream(
            stream,
            attachment,
            self._release_attachment,
        )

    def cancel(self) -> None:
        """Cancel all current opens/streams while leaving the watcher reusable."""

        with self._lock:
            if self._closed:
                return
            self._cancel_generation += 1
            attachments = tuple(self._active_attachments.values())
            self._active_attachments.clear()
        self._close_attachments(attachments)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_generation += 1
            attachments = tuple(self._active_attachments.values())
            self._active_attachments.clear()
        self._close_attachments(attachments)

    @staticmethod
    def _close_attachments(
        attachments: tuple[AdbTransportListWatchAttachment, ...],
    ) -> None:
        first_error: BaseException | None = None
        for attachment in attachments:
            try:
                attachment.close()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _release_attachment(
        self,
        attachment: AdbTransportListWatchAttachment,
    ) -> None:
        with self._lock:
            key = id(attachment)
            if self._active_attachments.get(key) is attachment:
                self._active_attachments.pop(key, None)


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchOpened:
    """A raw watch stream and its initial complete transport list were established."""

    session: AdbTransportListWatchStream
    initial: AdbTransportList

    def __post_init__(self) -> None:
        if not isinstance(self.session, AdbTransportListWatchStream):
            raise TypeError("session must satisfy AdbTransportListWatchStream")
        if not isinstance(self.initial, AdbTransportList):
            raise TypeError("initial must be AdbTransportList")

    @property
    def stream(self) -> AdbTransportListWatchStream:
        """Return the raw stream; the ``session`` field is retained for compatibility."""

        return self.session


@dataclass(frozen=True, slots=True)
class AdbTransportListWatchOpenCancelled:
    """Opening was interrupted before an initial list was established."""


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


class _FailureNormalizingAdbTransportListWatchStream:
    """Translate ADB request errors from an established stream into typed watch errors."""

    def __init__(self, stream: AdbTransportListWatchStream) -> None:
        if not isinstance(stream, AdbTransportListWatchStream):
            raise TypeError("stream must satisfy AdbTransportListWatchStream")
        self._stream = stream

    @property
    def initial(self) -> AdbTransportList:
        try:
            return self._stream.initial
        except BaseException as exc:
            _raise_normalized_watch_error(exc)
            raise AssertionError("unreachable")

    def updates(self) -> Iterator[AdbTransportList]:
        try:
            yield from self._stream.updates()
        except BaseException as exc:
            _raise_normalized_watch_error(exc)
            raise AssertionError("unreachable")

    def close(self) -> None:
        self._stream.close()


def open_transport_list_watch(
    watcher: AdbTransportListWatcher,
    address: TcpAddress,
) -> AdbTransportListWatchOpenResult:
    """Open one watcher session and normalize expected startup outcomes.

    The watcher is long-lived; the returned stream is short-lived and owns the low-level
    attachment for this single open. Server-lifetime binding remains a controller concern.
    """

    if not isinstance(watcher, AdbTransportListWatcher):
        raise TypeError("watcher must satisfy AdbTransportListWatcher")
    if not isinstance(address, TcpAddress):
        raise TypeError("address must be TcpAddress")

    try:
        stream = watcher.open(address)
    except AdbTransportListWatchCancelledError:
        return AdbTransportListWatchOpenCancelled()
    except BaseException as exc:
        failure = _watch_failure_from_exception(exc)
        if failure is None:
            raise
        return AdbTransportListWatchOpenFailed(failure)

    if stream is None:
        return AdbTransportListWatchOpenCancelled()
    if not isinstance(stream, AdbTransportListWatchStream):
        raise TypeError("transport-list watcher must return AdbTransportListWatchStream or None")

    normalized_stream = _FailureNormalizingAdbTransportListWatchStream(stream)
    try:
        initial = normalized_stream.initial
    except AdbTransportListWatchCancelledError:
        normalized_stream.close()
        return AdbTransportListWatchOpenCancelled()
    except AdbTransportListWatchError as exc:
        normalized_stream.close()
        return AdbTransportListWatchOpenFailed(exc.failure)
    except BaseException:
        normalized_stream.close()
        raise

    if not isinstance(initial, AdbTransportList):
        normalized_stream.close()
        raise TypeError("transport-list watch stream initial must be AdbTransportList")
    return AdbTransportListWatchOpened(normalized_stream, initial)


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
    "AdbTransportListWatchAttachment",
    "AdbTransportListWatchOpenCancelled",
    "AdbTransportListWatchOpenFailed",
    "AdbTransportListWatchOpened",
    "AdbTransportListWatchOpenResult",
    "AdbTransportListWatcher",
    "ReusableAdbTransportListWatcher",
    "open_transport_list_watch",
]
