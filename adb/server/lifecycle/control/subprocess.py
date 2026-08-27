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
from adb.server.lifecycle.control.backend import (
    AdbServerBackendFailed,
    AdbServerBackendOperation,
    AdbServerBackendOperationBlocked,
    AdbServerBackendOperationInProgress,
    AdbServerBackendResult,
    AdbServerBackendSatisfied,
    AdbServerBackendSucceeded,
    _require_owned_release_endpoint,
)


class SubprocessAdbServerBackend:
    """Adapt one owned foreground ADB server process to the server-backend port.

    The adapter owns backend attachment state and translates subprocess outcomes into lifecycle
    results.
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
    ) -> AdbServerBackendOperationInProgress | AdbServerBackendOperationBlocked | None:
        with self._operation_state_lock:
            active_operation = self._active_operation
            if active_operation is operation:
                return AdbServerBackendOperationInProgress(
                    operation,
                    f"ADB server backend {operation.value} is already in progress",
                )
            if active_operation is not None:
                return AdbServerBackendOperationBlocked(
                    (
                        f"ADB server backend {operation.value} cannot begin while "
                        f"{active_operation.value} is in progress"
                    ),
                    blocking_operation=active_operation,
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
    ) -> AdbServerBackendResult:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        operation = AdbServerBackendOperation.ACQUIRE
        unavailable = self._begin_operation(operation)
        if unavailable is not None:
            return unavailable

        try:
            attachment = self._attachment
            if attachment is not None:
                if not attachment.active:
                    # A prior unconfirmed cleanup can become provably complete later.  Once the
                    # child is observed exited, the stale implementation attachment owns no resource.
                    self._attachment = None
                    self._attachment_usable = False
                elif not self._attachment_usable:
                    return AdbServerBackendOperationBlocked(
                        "a prior ADB server backend cleanup is still converging",
                        blocking_operation=AdbServerBackendOperation.RELEASE,
                    )
                elif endpoint is None or attachment.endpoint == endpoint:
                    return AdbServerBackendSatisfied(attachment.endpoint)
                else:
                    return AdbServerBackendOperationBlocked(
                        "a different ADB server backend attachment is already staged"
                    )

            try:
                attachment = self._factory.create(endpoint)
            except _AdbServerSubprocessStartupCleanupUnconfirmed as exc:
                self._attachment = exc.attachment
                self._attachment_usable = False
                return AdbServerBackendFailed(
                    "ADB subprocess backend acquire failed and child-process cleanup "
                    "could not be completed"
                )
            except _AdbServerSubprocessStartError as exc:
                return AdbServerBackendFailed(str(exc))

            self._attachment = attachment
            self._attachment_usable = True
            return AdbServerBackendSucceeded(attachment.endpoint)
        finally:
            self._end_operation(operation)

    def release(self, endpoint: AdbServerEndpoint) -> AdbServerBackendResult:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")

        operation = AdbServerBackendOperation.RELEASE
        unavailable = self._begin_operation(operation)
        if unavailable is not None:
            return unavailable

        try:
            attachment = self._attachment
            if attachment is None:
                return AdbServerBackendSatisfied(endpoint)
            _require_owned_release_endpoint(attachment.endpoint, endpoint)

            try:
                attachment.close()
            except _AdbServerSubprocessTerminationUnconfirmed:
                # Keep ownership until termination is observable.  A failed release makes the
                # attachment unavailable for a subsequent acquire even if its child is still alive.
                self._attachment_usable = False
                return AdbServerBackendFailed(
                    "ADB subprocess backend could not release its owned attachment"
                )

            self._attachment = None
            self._attachment_usable = False
            return AdbServerBackendSucceeded(endpoint)
        finally:
            self._end_operation(operation)


__all__ = ["SubprocessAdbServerBackend"]
