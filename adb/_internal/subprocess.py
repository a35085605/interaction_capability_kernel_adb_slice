from __future__ import annotations

from datetime import datetime, timezone
import math
import subprocess

from adb.server.endpoint import AdbServerEndpoint
from adb.transport.selection import AdbTransportById, AdbTransportBySerial, AdbTransportSelector
from native_attempt import NativeAttemptResult, NativeAttemptStatus, NativeCompletionScope


def normalize_executable(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("ADB executable must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("ADB executable cannot be empty")
    return normalized


def normalize_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("ADB subprocess timeout must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError("ADB subprocess timeout must be finite and greater than zero")
    return normalized


def selector_args(selector: AdbTransportSelector) -> list[str]:
    if isinstance(selector, AdbTransportBySerial):
        return ["-s", selector.serial.value]
    if isinstance(selector, AdbTransportById):
        return ["-t", str(selector.transport_id.value)]
    raise TypeError("selector must be an ADB transport selector")


def server_args(endpoint: AdbServerEndpoint) -> list[str]:
    if not isinstance(endpoint, AdbServerEndpoint):
        raise TypeError("endpoint must be AdbServerEndpoint")
    return ["-H", endpoint.host, "-P", str(endpoint.port)]


def run_adb(
    executable: str,
    timeout_seconds: float,
    args: list[str],
    *,
    input_text: str | None = None,
) -> NativeAttemptResult:
    started_at = datetime.now(timezone.utc)
    run_kwargs: dict[str, object] = {
        "capture_output": True,
        "text": True,
        "check": False,
        "timeout": timeout_seconds,
    }
    if input_text is not None:
        run_kwargs["input"] = input_text
    try:
        completed = subprocess.run([executable, *args], **run_kwargs)
    except subprocess.TimeoutExpired as exc:
        return NativeAttemptResult(
            status=NativeAttemptStatus.TIMED_OUT,
            completion_scope=None,
            backend_id="adb-subprocess",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            native_code=type(exc).__name__,
            diagnostic=str(exc),
        )
    except OSError as exc:
        return NativeAttemptResult(
            status=NativeAttemptStatus.FAILED,
            completion_scope=None,
            backend_id="adb-subprocess",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            native_code=type(exc).__name__,
            diagnostic=str(exc),
        )

    diagnostic = "\n".join(
        part
        for part in (completed.stdout.strip(), completed.stderr.strip())
        if part
    ) or None
    return NativeAttemptResult(
        status=(
            NativeAttemptStatus.SUCCEEDED
            if completed.returncode == 0
            else NativeAttemptStatus.FAILED
        ),
        completion_scope=NativeCompletionScope.PROCESS_EXIT,
        backend_id="adb-subprocess",
        started_at=started_at,
        finished_at=datetime.now(timezone.utc),
        native_code=str(completed.returncode),
        diagnostic=diagnostic,
    )


__all__ = [
    "normalize_executable",
    "normalize_timeout",
    "run_adb",
    "selector_args",
    "server_args",
]
