from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import math
import os
import subprocess
from threading import Lock, RLock
from time import monotonic, sleep
from typing import Final


if os.name != "nt":
    raise ImportError("process_lifecycle.windows is only available on Windows")

import _winapi


_CREATE_SUSPENDED: Final = 0x00000004

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION: Final = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION: Final = 9

_DUPLICATE_SAME_ACCESS: Final = 0x00000002
_FORCED_EXIT_CODE: Final = 1

_MAX_FINITE_WAIT_MS: Final = 0xFFFFFFFE

_spawn_lock = Lock()
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


class WindowsProcessLifecycleError(RuntimeError):
    """Base error for Windows owned-process lifecycle operations."""


class WindowsProcessStartError(WindowsProcessLifecycleError):
    """A process could not be started under lifecycle ownership."""


class WindowsProcessWaitError(WindowsProcessLifecycleError):
    """Process or Job status observation failed."""


class WindowsProcessWaitTimeout(TimeoutError):
    """A root-process or process-tree wait timed out."""


class WindowsProcessTerminationUnconfirmed(WindowsProcessLifecycleError):
    """Forced cleanup was requested but could not be confirmed."""


@dataclass(frozen=True, slots=True)
class ProcessExit:
    code: int


@dataclass(frozen=True, slots=True)
class WindowsProcessSpec:
    argv: tuple[str, ...]
    executable: str | None = None
    cwd: str | None = None
    env: Mapping[str, str] | None = None
    inherited_handles: tuple[int, ...] = ()
    creation_flags: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple):
            raise TypeError("argv must be tuple[str, ...]")
        if not self.argv:
            raise ValueError("argv must not be empty")
        if any(not isinstance(value, str) for value in self.argv):
            raise TypeError("argv must contain only str")
        if any("\0" in value for value in self.argv):
            raise ValueError("argv must not contain NUL")

        if self.executable is not None:
            if not isinstance(self.executable, str):
                raise TypeError("executable must be str or None")
            if not self.executable:
                raise ValueError("executable must not be empty")
            if "\0" in self.executable:
                raise ValueError("executable must not contain NUL")

        if self.cwd is not None:
            if not isinstance(self.cwd, str):
                raise TypeError("cwd must be str or None")
            if "\0" in self.cwd:
                raise ValueError("cwd must not contain NUL")

        if not isinstance(self.inherited_handles, tuple):
            raise TypeError("inherited_handles must be tuple[int, ...]")
        for handle in self.inherited_handles:
            if isinstance(handle, bool) or not isinstance(handle, int):
                raise TypeError("inherited_handles must contain only int handles")

        if isinstance(self.creation_flags, bool) or not isinstance(
            self.creation_flags, int
        ):
            raise TypeError("creation_flags must be int")
        if not 0 <= self.creation_flags <= 0xFFFFFFFF:
            raise ValueError("creation_flags must fit in DWORD")


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


def _unique_handles(handles: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(handles))


def _best_effort_abort(
    *,
    process: int | None,
    thread: int | None,
    job: int | None,
    assigned_to_job: bool,
) -> None:
    if process is not None:
        try:
            if assigned_to_job and job is not None:
                _kernel32.TerminateJobObject(
                    wintypes.HANDLE(job),
                    _FORCED_EXIT_CODE,
                )
            else:
                _winapi.TerminateProcess(
                    process,
                    _FORCED_EXIT_CODE,
                )
        except BaseException:
            pass

    if thread is not None:
        try:
            _winapi.CloseHandle(thread)
        except BaseException:
            pass

    if process is not None:
        try:
            _winapi.CloseHandle(process)
        except BaseException:
            pass

    if job is not None:
        try:
            _winapi.CloseHandle(job)
        except BaseException:
            pass


class WindowsOwnedProcess:
    """A root process and the descendant tree contained by its private Job."""

    def __init__(
        self,
        *,
        pid: int,
        process_handle: int,
        job_handle: int,
    ) -> None:
        self._pid = pid
        self._process_handle: int | None = process_handle
        self._job_handle: int | None = job_handle

        self._exit: ProcessExit | None = None
        self._closed = False

        self._state_lock = RLock()
        self._close_lock = Lock()

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    def poll(self) -> ProcessExit | None:
        with self._state_lock:
            if self._exit is not None:
                return self._exit

            if self._process_handle is None:
                return None

            process = _duplicate_handle(self._process_handle)

        try:
            if not _wait_handle(process, 0.0):
                return None
            exit_status = ProcessExit(
                int(_winapi.GetExitCodeProcess(process))
            )
        finally:
            _winapi.CloseHandle(process)

        with self._state_lock:
            if self._exit is None:
                self._exit = exit_status
            return self._exit

    def wait(
        self,
        timeout_seconds: float | None = None,
    ) -> ProcessExit:
        timeout = _normalize_timeout(timeout_seconds)

        with self._state_lock:
            if self._exit is not None:
                return self._exit

            if self._process_handle is None:
                raise WindowsProcessWaitError(
                    "process handle has already been closed"
                )

            process = _duplicate_handle(self._process_handle)

        try:
            if not _wait_handle(process, timeout):
                raise WindowsProcessWaitTimeout(
                    f"timed out waiting for process {self._pid}"
                )

            exit_status = ProcessExit(
                int(_winapi.GetExitCodeProcess(process))
            )
        finally:
            _winapi.CloseHandle(process)

        with self._state_lock:
            if self._exit is None:
                self._exit = exit_status
            return self._exit

    def active_process_count(self) -> int:
        with self._state_lock:
            if self._job_handle is None:
                return 0
            job = _duplicate_handle(self._job_handle)

        try:
            return _active_processes(job)
        finally:
            _winapi.CloseHandle(job)

    def wait_tree(
        self,
        timeout_seconds: float | None = None,
    ) -> None:
        timeout = _normalize_timeout(timeout_seconds)

        with self._state_lock:
            if self._job_handle is None:
                return
            job = _duplicate_handle(self._job_handle)

        try:
            if not _wait_job_empty(job, timeout):
                raise WindowsProcessWaitTimeout(
                    f"timed out waiting for process tree rooted at {self._pid}"
                )
        finally:
            _winapi.CloseHandle(job)

    def terminate_tree(
        self,
        exit_code: int = _FORCED_EXIT_CODE,
    ) -> None:
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise TypeError("exit_code must be int")
        if not 0 <= exit_code <= 0xFFFFFFFF:
            raise ValueError("exit_code must fit in DWORD")

        with self._state_lock:
            if self._job_handle is None:
                return
            job = _duplicate_handle(self._job_handle)

        try:
            if _active_processes(job) == 0:
                return

            if not _kernel32.TerminateJobObject(
                wintypes.HANDLE(job),
                exit_code,
            ):
                # The process tree may have raced to natural termination.
                if _active_processes(job) != 0:
                    raise _last_error("TerminateJobObject")
        finally:
            _winapi.CloseHandle(job)

    def close(
        self,
        timeout_seconds: float | None = 5.0,
    ) -> None:
        """Force-clean the owned tree and release all Win32 handles.

        This is intentionally not a graceful shutdown API. Consumers may
        first perform their own protocol-specific graceful shutdown, wait(),
        and use close() only as the ownership cleanup/fallback.
        """

        timeout = _normalize_timeout(timeout_seconds)

        with self._close_lock:
            with self._state_lock:
                if self._closed:
                    return

                job = self._job_handle
                process = self._process_handle

            if job is not None and _active_processes(job) != 0:
                if not _kernel32.TerminateJobObject(
                    wintypes.HANDLE(job),
                    _FORCED_EXIT_CODE,
                ):
                    if _active_processes(job) != 0:
                        raise WindowsProcessTerminationUnconfirmed(
                            str(_last_error("TerminateJobObject"))
                        )

                if not _wait_job_empty(job, timeout):
                    raise WindowsProcessTerminationUnconfirmed(
                        "owned process tree did not terminate "
                        "before cleanup timeout"
                    )

            exit_status: ProcessExit | None = None

            if process is not None:
                if not _wait_handle(process, 0.0):
                    raise WindowsProcessTerminationUnconfirmed(
                        "root process remained active after Job became empty"
                    )

                exit_status = ProcessExit(
                    int(_winapi.GetExitCodeProcess(process))
                )

            # Only release ownership handles after termination is confirmed.
            with self._state_lock:
                if self._process_handle is not None:
                    _winapi.CloseHandle(self._process_handle)
                    self._process_handle = None

                if self._job_handle is not None:
                    _winapi.CloseHandle(self._job_handle)
                    self._job_handle = None

                if exit_status is not None and self._exit is None:
                    self._exit = exit_status

                self._closed = True

    def __del__(self) -> None:
        # Never block or raise during finalization.
        #
        # Close Job first so JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE remains the
        # last-resort containment guarantee when explicit close() was skipped.
        job = getattr(self, "_job_handle", None)
        process = getattr(self, "_process_handle", None)

        if job is not None:
            try:
                _winapi.CloseHandle(job)
            except BaseException:
                pass

        if process is not None:
            try:
                _winapi.CloseHandle(process)
            except BaseException:
                pass


class WindowsProcessLifecycleManager:
    """Create privately-owned Windows process trees."""

    def spawn(
        self,
        spec: WindowsProcessSpec,
    ) -> WindowsOwnedProcess:
        if not isinstance(spec, WindowsProcessSpec):
            raise TypeError("spec must be WindowsProcessSpec")

        handles = _unique_handles(spec.inherited_handles)

        startupinfo = subprocess.STARTUPINFO(
            lpAttributeList={
                "handle_list": list(handles),
            }
        )

        executable = spec.executable or spec.argv[0]
        command_line = subprocess.list2cmdline(spec.argv)

        job: int | None = None
        process: int | None = None
        thread: int | None = None
        pid: int | None = None
        assigned_to_job = False

        try:
            job = _create_job()

            # STARTUPINFOEX handle-list inheritance still requires each listed
            # handle to be inheritable at CreateProcess time. Keep that window
            # serialized and restore the caller's original state immediately.
            with _spawn_lock:
                original_inheritance: list[tuple[int, bool]] = []

                try:
                    for handle in handles:
                        original = os.get_handle_inheritable(handle)
                        original_inheritance.append((handle, original))

                        if not original:
                            os.set_handle_inheritable(handle, True)

                    process, thread, pid, _ = _winapi.CreateProcess(
                        executable,
                        command_line,
                        None,
                        None,
                        bool(handles),
                        spec.creation_flags | _CREATE_SUSPENDED,
                        spec.env,
                        spec.cwd,
                        startupinfo,
                    )

                finally:
                    restore_failure: BaseException | None = None

                    for handle, original in reversed(original_inheritance):
                        try:
                            os.set_handle_inheritable(handle, original)
                        except BaseException as exc:
                            if restore_failure is None:
                                restore_failure = exc

                if restore_failure is not None:
                    raise WindowsProcessStartError(
                        "failed to restore inherited-handle state: "
                        f"{restore_failure}"
                    ) from restore_failure

            assert job is not None
            assert process is not None
            assert thread is not None
            assert pid is not None

            if not _kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(job),
                wintypes.HANDLE(process),
            ):
                raise _last_error("AssignProcessToJobObject")

            assigned_to_job = True

            if _kernel32.ResumeThread(
                wintypes.HANDLE(thread)
            ) == 0xFFFFFFFF:
                raise _last_error("ResumeThread")

            _winapi.CloseHandle(thread)
            thread = None

            return WindowsOwnedProcess(
                pid=int(pid),
                process_handle=int(process),
                job_handle=int(job),
            )

        except BaseException as exc:
            _best_effort_abort(
                process=process,
                thread=thread,
                job=job,
                assigned_to_job=assigned_to_job,
            )

            if isinstance(exc, WindowsProcessStartError):
                raise

            raise WindowsProcessStartError(
                f"failed to establish owned Windows process: {exc}"
            ) from exc


__all__ = [
    "ProcessExit",
    "WindowsOwnedProcess",
    "WindowsProcessLifecycleError",
    "WindowsProcessLifecycleManager",
    "WindowsProcessSpec",
    "WindowsProcessStartError",
    "WindowsProcessTerminationUnconfirmed",
    "WindowsProcessWaitError",
    "WindowsProcessWaitTimeout",
]
