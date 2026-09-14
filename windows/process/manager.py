from __future__ import annotations

from ctypes import wintypes
import os
import subprocess
from threading import Lock

import _winapi

from windows.process._native import (
    _CREATE_SUSPENDED,
    _create_job,
    _kernel32,
    _last_error,
)
from windows.process.errors import WindowsProcessStartError
from windows.process.model import WindowsProcessSpec
from windows.process.owned import WindowsOwnedProcess, WindowsRetainedProcess


_spawn_lock = Lock()


def _unique_handles(handles: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(handles))


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
                        f"{restore_failure}",
                        original_error=restore_failure,
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
            if process is not None and job is not None and pid is not None:
                retained_process = WindowsRetainedProcess(
                    pid=int(pid),
                    process_handle=int(process),
                    thread_handle=None if thread is None else int(thread),
                    job_handle=int(job),
                    assigned_to_job=assigned_to_job,
                )
                original_error = (
                    exc.original_error
                    if isinstance(exc, WindowsProcessStartError)
                    and exc.original_error is not None
                    else exc
                )
                diagnostic = (
                    str(exc)
                    if isinstance(exc, WindowsProcessStartError)
                    else f"failed to establish owned Windows process: {exc}"
                )
                raise WindowsProcessStartError(
                    diagnostic,
                    retained_process=retained_process,
                    original_error=original_error,
                ) from exc

            # No process was created, so the private Job is empty and can be
            # released immediately without creating a retained cleanup token.
            if thread is not None:
                try:
                    _winapi.CloseHandle(thread)
                except BaseException:
                    pass
            if job is not None:
                try:
                    _winapi.CloseHandle(job)
                except BaseException:
                    pass

            if isinstance(exc, WindowsProcessStartError):
                raise

            raise WindowsProcessStartError(
                f"failed to establish owned Windows process: {exc}",
                original_error=exc,
            ) from exc


__all__ = ["WindowsProcessLifecycleManager"]
