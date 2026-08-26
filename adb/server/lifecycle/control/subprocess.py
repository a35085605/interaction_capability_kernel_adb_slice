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
from adb.server.lifecycle.control.errors import AdbServerAttachmentMismatchError
from adb.server.lifecycle.control.backend import (
    AdbServerAcquireFailed,
    AdbServerAcquireResult,
    AdbServerAcquireSatisfied,
    AdbServerAcquireSucceeded,
    AdbServerBackendOperation,
    AdbServerBackendOperationInProgress,
    AdbServerReleaseFailed,
    AdbServerReleaseNotStaged,
    AdbServerReleaseResult,
    AdbServerReleaseSucceeded,
    require_backend_release_endpoint,
)


class SubprocessAdbServerBackend:
    """Adapt one owned foreground ADB server process to the server-backend domain port.

    Subprocess creation, socket activation, readiness probing, and process termination are private
    infrastructure details.  This adapter owns only the backend-visible attachment slot and the
    translation of infrastructure outcomes into lifecycle-control result values.
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
        self._operation_state_lock = Lock()
        self._active_operation: AdbServerBackendOperation | None = None
        self._attachment: _OwnedAdbServerProcess | None = None
        self._attachment_usable = False

    def _begin_operation(
        self,
        operation: AdbServerBackendOperation,
    ) -> AdbServerBackendOperationInProgress | None:
        with self._operation_state_lock:
            active_operation = self._active_operation
            if active_operation is not None:
                return AdbServerBackendOperationInProgress(
                    active_operation,
                    "another ADB server backend operation is already in progress",
                )
            self._active_operation = operation
            return None

    def _end_operation(self, operation: AdbServerBackendOperation) -> None:
        with self._operation_state_lock:
            if self._active_operation is not operation:
                raise RuntimeError("ADB server backend operation state is inconsistent")
            self._active_operation = None

    def acquire(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquireResult:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        operation = AdbServerBackendOperation.ACQUIRE
        in_progress = self._begin_operation(operation)
        if in_progress is not None:
            return in_progress

        try:
            attachment = self._attachment
            if attachment is not None:
                if not attachment.active:
                    # A prior unconfirmed cleanup can become provably complete later.  Once the
                    # child is observed exited, the stale implementation attachment owns no resource.
                    self._attachment = None
                    self._attachment_usable = False
                elif not self._attachment_usable:
                    return AdbServerBackendOperationInProgress(
                        AdbServerBackendOperation.RELEASE,
                        "a prior ADB server backend cleanup is still converging",
                    )
                elif endpoint is None or attachment.endpoint == endpoint:
                    return AdbServerAcquireSatisfied(attachment.endpoint)
                else:
                    raise AdbServerAttachmentMismatchError(
                        "requested endpoint differs from the already acquired ADB server attachment"
                    )

            try:
                attachment = self._factory.create(endpoint)
            except _AdbServerSubprocessStartupCleanupUnconfirmed as exc:
                self._attachment = exc.attachment
                self._attachment_usable = False
                return AdbServerAcquireFailed(
                    "ADB subprocess backend acquire failed and child-process cleanup "
                    "could not be completed"
                )
            except _AdbServerSubprocessStartError as exc:
                return AdbServerAcquireFailed(str(exc))

            self._attachment = attachment
            self._attachment_usable = True
            return AdbServerAcquireSucceeded(attachment.endpoint)
        finally:
            self._end_operation(operation)

    def release(self, endpoint: AdbServerEndpoint) -> AdbServerReleaseResult:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")

        operation = AdbServerBackendOperation.RELEASE
        in_progress = self._begin_operation(operation)
        if in_progress is not None:
            return in_progress

        try:
            attachment = self._attachment
            if attachment is None:
                return AdbServerReleaseNotStaged(endpoint)
            require_backend_release_endpoint(attachment.endpoint, endpoint)

            try:
                attachment.close()
            except _AdbServerSubprocessTerminationUnconfirmed as exc:
                # Keep ownership until termination is observable.  A failed release makes the
                # attachment unavailable for a subsequent acquire even if its child is still alive.
                self._attachment_usable = False
                return AdbServerReleaseFailed(
                    "ADB subprocess backend could not release its owned attachment"
                )

            self._attachment = None
            self._attachment_usable = False
            return AdbServerReleaseSucceeded(endpoint)
        finally:
            self._end_operation(operation)


__all__ = ["SubprocessAdbServerBackend"]
