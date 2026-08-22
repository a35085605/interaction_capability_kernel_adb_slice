from __future__ import annotations

from typing import Protocol, runtime_checkable

from adb.server.lifecycle.handle import AdbServerNativeError, AdbServerProcessLifetime
from adb.server.endpoint import AdbServerEndpoint


class AdbServerLaunchError(AdbServerNativeError):
    """Creation of one fresh native ADB server process failed."""


@runtime_checkable
class AdbServerLauncher(Protocol):
    """Process-backend primitive that launches one fresh ADB server process.

    A successful launch returns a backend process-lifetime capability.  That capability does
    not define ADB ownership; creation provenance and lifecycle responsibility are modeled
    separately by ``adb.server.ownership``.
    """

    def launch(self, endpoint: AdbServerEndpoint | None = None) -> AdbServerProcessLifetime:
        """Launch one fresh process lifetime, optionally binding this launch to ``endpoint``."""
        ...


__all__ = ["AdbServerLaunchError", "AdbServerLauncher"]
