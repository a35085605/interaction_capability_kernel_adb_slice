from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint

if TYPE_CHECKING:
    from adb.server.status.model import AdbServerStatus
    from adb.server.status.reader import AdbServerStatusReader
    from native_attempt import NativeAttemptResult


@runtime_checkable
class AdbServerController(Protocol):
    """Service-level control of an ADB server endpoint.

    A controller deliberately carries no ownership or generation guarantee. ``start`` may
    create a server or simply observe an already-running service, while ``close`` may request
    service termination without proving that one exact native lifetime was terminated.

    Ownership is policy, not capability: an adopted or unknown server can still be technically
    terminable through this controller. Higher layers decide whether exercising that capability is
    authorized for the server's ADB-level creation provenance.
    """

    @property
    def endpoint(self) -> AdbServerEndpoint: ...

    def status(self) -> AdbServerStatus: ...

    def start(self) -> NativeAttemptResult: ...

    def close(self) -> NativeAttemptResult: ...


@dataclass(frozen=True, slots=True)
class SubprocessAdbServerController:
    """Control one ADB server endpoint through ordinary ``adb`` server commands.

    This is intentionally weaker than an exact process-lifetime backend: it uses ``start-server`` and
    ``kill-server`` and therefore does not claim exact-lifetime teardown authority.
    """

    endpoint: AdbServerEndpoint = field(default_factory=AdbServerEndpoint)
    executable: str = "adb"
    timeout_seconds: float = 10.0
    _status_reader: AdbServerStatusReader | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        from adb._internal.subprocess import normalize_executable, normalize_timeout

        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        object.__setattr__(self, "executable", normalize_executable(self.executable))
        object.__setattr__(self, "timeout_seconds", normalize_timeout(self.timeout_seconds))
        reader = self._status_reader
        if reader is None:
            from adb.server.status.reader import SmartSocketAdbServerStatusReader

            reader = SmartSocketAdbServerStatusReader()
            object.__setattr__(self, "_status_reader", reader)
        if not callable(getattr(reader, "read", None)):
            raise TypeError("_status_reader must satisfy AdbServerStatusReader")

    def status(self) -> AdbServerStatus:
        assert self._status_reader is not None
        return self._status_reader.read(self.endpoint)

    def start(self) -> NativeAttemptResult:
        from adb._internal.subprocess import run_adb, server_args

        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.endpoint), "start-server"],
        )

    def close(self) -> NativeAttemptResult:
        from adb._internal.subprocess import run_adb, server_args

        return run_adb(
            self.executable,
            self.timeout_seconds,
            [*server_args(self.endpoint), "kill-server"],
        )


__all__ = [
    "AdbServerController",
    "AdbServerMutationReservedError",
    "SubprocessAdbServerController",
]


# Public compatibility import: mutation authority is owned by ``adb.server.coordination``.
from adb.server.coordination import AdbServerMutationReservedError  # noqa: E402
