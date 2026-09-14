from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import os
from time import monotonic, sleep
from typing import Final

from windows.process.errors import (
    WindowsProcessLifecycleError,
    WindowsProcessWaitError,
)

if os.name != "nt":
    raise ImportError("windows.process is only available on Windows")

import _winapi


_CREATE_SUSPENDED: Final = 0x00000004

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION: Final = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final = 9

_DUPLICATE_SAME_ACCESS: Final = 0x00000002
_FORCED_EXIT_CODE: Final = 1

_MAX_FINITE_WAIT_MS: Final = 0xFFFFFFFE

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


_kernel32.CreateJobObjectW.argtypes = (
    wintypes.LPVOID,
    wintypes.LPCWSTR,
)
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE

_kernel32.SetInformationJobObject.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
)
_kernel32.SetInformationJobObject.restype = wintypes.BOOL

_kernel32.QueryInformationJobObject.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
_kernel32.QueryInformationJobObject.restype = wintypes.BOOL

_kernel32.AssignProcessToJobObject.argtypes = (
    wintypes.HANDLE,
    wintypes.HANDLE,
)
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

_kernel32.TerminateJobObject.argtypes = (
    wintypes.HANDLE,
    wintypes.UINT,
)
_kernel32.TerminateJobObject.restype = wintypes.BOOL

_kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
_kernel32.ResumeThread.restype = wintypes.DWORD

_kernel32.GetCurrentProcess.argtypes = ()
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE

_kernel32.DuplicateHandle.argtypes = (
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.DWORD,
    wintypes.BOOL,
    wintypes.DWORD,
)
_kernel32.DuplicateHandle.restype = wintypes.BOOL

def _last_error(operation: str) -> WindowsProcessLifecycleError:
    code = ctypes.get_last_error()
    detail = ctypes.FormatError(code).strip()
    return WindowsProcessLifecycleError(
        f"{operation} failed with Windows error {code}: {detail}"
    )


def _normalize_timeout(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError(
            "timeout_seconds must be a non-negative finite number or None"
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(
            "timeout_seconds must be a non-negative finite number or None"
        ) from exc

    if not math.isfinite(result) or result < 0.0:
        raise ValueError(
            "timeout_seconds must be a non-negative finite number or None"
        )
    return result


def _duplicate_handle(handle: int) -> int:
    process = _kernel32.GetCurrentProcess()
    duplicate = wintypes.HANDLE()

    if not _kernel32.DuplicateHandle(
        process,
        wintypes.HANDLE(handle),
        process,
        ctypes.byref(duplicate),
        0,
        False,
        _DUPLICATE_SAME_ACCESS,
    ):
        raise _last_error("DuplicateHandle")

    return int(duplicate.value)


def _wait_handle(handle: int, timeout_seconds: float | None) -> bool:
    timeout = _normalize_timeout(timeout_seconds)

    if timeout is None:
        result = _winapi.WaitForSingleObject(handle, _winapi.INFINITE)
        if result == _winapi.WAIT_OBJECT_0:
            return True
        raise WindowsProcessWaitError(
            f"unexpected WaitForSingleObject result: {result!r}"
        )

    deadline = monotonic() + timeout

    while True:
        remaining = max(0.0, deadline - monotonic())
        milliseconds = min(
            _MAX_FINITE_WAIT_MS,
            math.ceil(remaining * 1000.0),
        )

        result = _winapi.WaitForSingleObject(handle, milliseconds)

        if result == _winapi.WAIT_OBJECT_0:
            return True

        if result == _winapi.WAIT_TIMEOUT:
            if monotonic() >= deadline:
                return False
            continue

        raise WindowsProcessWaitError(
            f"unexpected WaitForSingleObject result: {result!r}"
        )


def _create_job() -> int:
    raw = _kernel32.CreateJobObjectW(None, None)
    if not raw:
        raise _last_error("CreateJobObjectW")

    job = int(raw)

    limits = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )

    if not _kernel32.SetInformationJobObject(
        wintypes.HANDLE(job),
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    ):
        error = _last_error("SetInformationJobObject")
        _winapi.CloseHandle(job)
        raise error

    return job


def _active_processes(job: int) -> int:
    accounting = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()

    if not _kernel32.QueryInformationJobObject(
        wintypes.HANDLE(job),
        _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        None,
    ):
        raise WindowsProcessWaitError(
            str(_last_error("QueryInformationJobObject"))
        )

    return int(accounting.ActiveProcesses)


def _wait_job_empty(
    job: int,
    timeout_seconds: float | None,
) -> bool:
    timeout = _normalize_timeout(timeout_seconds)
    deadline = None if timeout is None else monotonic() + timeout

    while True:
        if _active_processes(job) == 0:
            return True

        if deadline is None:
            sleep(0.01)
            continue

        remaining = deadline - monotonic()
        if remaining <= 0.0:
            return False

        sleep(min(0.01, remaining))
