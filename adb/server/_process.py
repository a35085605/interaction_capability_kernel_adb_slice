"""Process-wide production composition for ADB server coordination."""

from adb.server.coordination import _ProcessAdbServerCoordinator
from adb.server.lifecycle.control.adapter.subprocess import SubprocessAdbServerController


_PROCESS_ADB_SERVER_COORDINATOR = _ProcessAdbServerCoordinator(
    SubprocessAdbServerController()
)


__all__: list[str] = []
