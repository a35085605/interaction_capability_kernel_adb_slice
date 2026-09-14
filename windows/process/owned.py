from __future__ import annotations

from ctypes import wintypes
from threading import Lock, RLock

import _winapi

from windows.process._native import (
    _FORCED_EXIT_CODE,
    _active_processes,
    _duplicate_handle,
    _kernel32,
    _last_error,
    _normalize_timeout,
    _wait_handle,
    _wait_job_empty,
)
from windows.process.errors import (
    WindowsProcessTerminationUnconfirmed,
    WindowsProcessWaitError,
    WindowsProcessWaitTimeout,
)
from windows.process.model import ProcessExit


class WindowsRetainedProcess:
    """Cleanup token for a process whose spawn did not complete.

    The process may or may not have been assigned to its private Job. Handles
    remain owned by this token until cleanup confirms that every owned process
    is terminated. This lets failed acquisition retain an unassigned suspended
    process instead of closing the only handles capable of controlling it.
    """

    def __init__(
        self,
        *,
        pid: int,
        process_handle: int,
        thread_handle: int | None,
        job_handle: int,
        assigned_to_job: bool,
    ) -> None:
        self._pid = pid
        self._process_handle: int | None = process_handle
        self._thread_handle: int | None = thread_handle
        self._job_handle: int | None = job_handle
        self._assigned_to_job = assigned_to_job
        self._closed = False

        self._state_lock = RLock()
        self._close_lock = Lock()

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def assigned_to_job(self) -> bool:
        return self._assigned_to_job

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    def close(
        self,
        timeout_seconds: float | None = 5.0,
    ) -> None:
        """Terminate the retained process state and release its handles.

        A failed cleanup keeps every still-open ownership handle on this token
        so callers may retry with the same resource.
        """

        timeout = _normalize_timeout(timeout_seconds)

        with self._close_lock:
            with self._state_lock:
                if self._closed:
                    return
                process = self._process_handle
                job = self._job_handle
                assigned_to_job = self._assigned_to_job

            if process is None:
                raise WindowsProcessTerminationUnconfirmed(
                    "retained process handle is unavailable before cleanup completed"
                )

            if assigned_to_job:
                if job is None:
                    raise WindowsProcessTerminationUnconfirmed(
                        "retained process is marked assigned but its Job handle is unavailable"
                    )

                if _active_processes(job) != 0:
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
                            "retained owned process tree did not terminate "
                            "before cleanup timeout"
                        )

                if not _wait_handle(process, timeout):
                    raise WindowsProcessTerminationUnconfirmed(
                        "retained root process remained active after Job cleanup"
                    )
            elif not _wait_handle(process, 0.0):
                try:
                    _winapi.TerminateProcess(process, _FORCED_EXIT_CODE)
                except BaseException as exc:
                    if not _wait_handle(process, 0.0):
                        raise WindowsProcessTerminationUnconfirmed(
                            f"failed to terminate retained unassigned process: {exc}"
                        ) from exc

                if not _wait_handle(process, timeout):
                    raise WindowsProcessTerminationUnconfirmed(
                        "retained unassigned process did not terminate "
                        "before cleanup timeout"
                    )

            # Termination is confirmed before relinquishing any remaining handles.
            with self._state_lock:
                if self._thread_handle is not None:
                    _winapi.CloseHandle(self._thread_handle)
                    self._thread_handle = None

                if self._process_handle is not None:
                    _winapi.CloseHandle(self._process_handle)
                    self._process_handle = None

                if self._job_handle is not None:
                    _winapi.CloseHandle(self._job_handle)
                    self._job_handle = None

                self._closed = True

    def __del__(self) -> None:
        # Finalization cannot wait or raise. Keep the Job close as a last-resort
        # containment mechanism, and explicitly terminate an unassigned root.
        process = getattr(self, "_process_handle", None)
        thread = getattr(self, "_thread_handle", None)
        job = getattr(self, "_job_handle", None)
        assigned_to_job = getattr(self, "_assigned_to_job", False)

        if process is not None and not assigned_to_job:
            try:
                _winapi.TerminateProcess(process, _FORCED_EXIT_CODE)
            except BaseException:
                pass

        if job is not None:
            try:
                _winapi.CloseHandle(job)
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


__all__ = ["WindowsOwnedProcess", "WindowsRetainedProcess"]
