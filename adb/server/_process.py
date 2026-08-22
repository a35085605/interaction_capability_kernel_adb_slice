"""Process-wide production composition for ADB server coordination."""

from adb.server.coordination import _ProcessAdbServerCoordinator
from adb.server.identity import _AdbServerSequence
from adb.server.lifecycle.control.adapter.subprocess import SubprocessAdbServerController


_SERVER_SEQUENCE = _AdbServerSequence()
_PROCESS_ADB_SERVER_COORDINATOR = _ProcessAdbServerCoordinator(
    SubprocessAdbServerController(_server_factory=_SERVER_SEQUENCE.next)
)


__all__: list[str] = []
