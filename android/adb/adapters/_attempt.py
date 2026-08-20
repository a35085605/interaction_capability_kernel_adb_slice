from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from adb._internal.client import AdbServiceClient, ShellV2Result
from adb.errors import AdbError, AdbTimeoutError
from adb.server import AdbServerEndpoint
from adb.transport import AdbTransportSelector
from native_attempt import NativeAttemptResult, NativeAttemptStatus, NativeCompletionScope


ClientFactory = Callable[[AdbServerEndpoint], AdbServiceClient]


def default_client_factory(endpoint: AdbServerEndpoint) -> AdbServiceClient:
    return AdbServiceClient(endpoint)


def shell_v2_attempt(
    endpoint: AdbServerEndpoint,
    selector: AdbTransportSelector,
    command: str,
    *,
    backend_id: str,
    client_factory: ClientFactory = default_client_factory,
) -> NativeAttemptResult:
    started_at = datetime.now(timezone.utc)
    try:
        result = client_factory(endpoint).shell_v2(selector, command)
    except AdbTimeoutError as exc:
        return NativeAttemptResult(
            status=NativeAttemptStatus.TIMED_OUT,
            completion_scope=None,
            backend_id=backend_id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            native_code=type(exc).__name__,
            diagnostic=str(exc),
        )
    except AdbError as exc:
        return NativeAttemptResult(
            status=NativeAttemptStatus.FAILED,
            completion_scope=None,
            backend_id=backend_id,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            native_code=type(exc).__name__,
            diagnostic=str(exc),
        )
    return native_result(backend_id, started_at, result)


def native_result(
    backend_id: str,
    started_at: datetime,
    result: ShellV2Result,
) -> NativeAttemptResult:
    diagnostic = result.stderr.decode("utf-8", errors="replace").strip() or None
    return NativeAttemptResult(
        status=(
            NativeAttemptStatus.SUCCEEDED
            if result.exit_code == 0
            else NativeAttemptStatus.FAILED
        ),
        completion_scope=NativeCompletionScope.PROCESS_EXIT,
        backend_id=backend_id,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        native_code=str(result.exit_code),
        diagnostic=diagnostic,
    )


__all__ = ["ClientFactory", "default_client_factory", "native_result", "shell_v2_attempt"]
