from __future__ import annotations

from threading import Lock

from adb._internal.server_subprocess import (
    _AdbServerSubprocessFactory,
    _AdbServerSubprocessStartError,
    _AdbServerSubprocessStartupCleanupUnconfirmed,
    _AdbServerSubprocessTerminationUnconfirmed,
    _OwnedAdbServerProcess,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.control.backend import require_backend_release_endpoint
from adb.server.lifecycle.control.errors import (
    AdbServerBackendBusyError,
    AdbServerNoAttachmentError,
    AdbServerStartError,
    AdbServerStopError,
)


class SubprocessAdbServerBackend:
    """Adapt one owned foreground ADB server process to the server-backend domain port.

    Subprocess creation, socket activation, readiness probing, and process termination are private
    infrastructure details.  This adapter owns only the backend-visible attachment slot and the
    translation of infrastructure outcomes into lifecycle-control domain errors.
    """

    def __init__(
        self,
        *,
        executable: str = "adb",
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
        probe_interval_seconds: float = 0.05,
        _factory: _AdbServerSubprocessFactory | None = None,
    ) -> None:
        if _factory is None:
            _factory = _AdbServerSubprocessFactory(
                executable=executable,
                startup_timeout_seconds=startup_timeout_seconds,
                shutdown_timeout_seconds=shutdown_timeout_seconds,
                probe_interval_seconds=probe_interval_seconds,
            )
        if not callable(getattr(_factory, "create", None)):
            raise TypeError("_factory must provide create()")

        self._factory = _factory
        self._operation_lock = Lock()
        self._attachment: _OwnedAdbServerProcess | None = None

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerEndpoint:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")
        if not self._operation_lock.acquire(blocking=False):
            raise AdbServerBackendBusyError(
                "another ADB server backend operation is already in progress"
            )

        try:
            attachment = self._attachment
            if attachment is not None:
                if attachment.active:
                    raise AdbServerBackendBusyError(
                        "an ADB server backend attachment already occupies this backend slot"
                    )
                # A prior unconfirmed cleanup can become provably complete later.  Once the child
                # is observed exited, the stale implementation attachment no longer owns resources.
                self._attachment = None

            try:
                attachment = self._factory.create(endpoint)
            except _AdbServerSubprocessStartupCleanupUnconfirmed as exc:
                self._attachment = exc.attachment
                raise AdbServerStartError(
                    "ADB subprocess backend acquire failed and child-process cleanup "
                    "could not be completed"
                ) from exc.termination_error
            except _AdbServerSubprocessStartError as exc:
                raise AdbServerStartError(str(exc)) from exc

            self._attachment = attachment
            return attachment.endpoint
        finally:
            self._operation_lock.release()

    def release(self, endpoint: AdbServerEndpoint) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not self._operation_lock.acquire(blocking=False):
            raise AdbServerBackendBusyError(
                "another ADB server backend operation is already in progress"
            )

        try:
            attachment = self._attachment
            if attachment is None:
                raise AdbServerNoAttachmentError(
                    "no ADB server backend attachment is owned"
                )
            require_backend_release_endpoint(attachment.endpoint, endpoint)

            try:
                attachment.close()
            except _AdbServerSubprocessTerminationUnconfirmed as exc:
                # Keep ownership until termination is observable.  The backend never forgets an
                # implementation resource merely because cleanup could not prove its disposal.
                raise AdbServerStopError(
                    "ADB subprocess backend could not release its owned attachment"
                ) from exc

            self._attachment = None
        finally:
            self._operation_lock.release()


__all__ = ["SubprocessAdbServerBackend"]
