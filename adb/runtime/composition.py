from __future__ import annotations

import os
from typing import cast

from lifecycle.resource.driver import ResourceDriver
from adb.adapters.server_process.lifecycle import AdbServerProcessLifecycle
from adb.runtime.server.bootstrap import create_adb_server_runtime
from adb.runtime.server.commands import AdbServerCommandPolicy
from adb.runtime.server.runtime import AdbServerRuntime
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.lifecycle import AdbServerLifecycle
from networking import TcpEndpoint


def create_adb_server_process_lifecycle(
    generation_issuer: AdbServerGenerationIssuer,
    *,
    executable: str = "adb",
    startup_timeout_seconds: float = 5.0,
    shutdown_timeout_seconds: float = 5.0,
    probe_interval_seconds: float = 0.05,
) -> AdbServerLifecycle:
    """Compose the platform process driver and its server-domain lifecycle adapter."""

    if not isinstance(generation_issuer, AdbServerGenerationIssuer):
        raise TypeError("generation_issuer must be AdbServerGenerationIssuer")

    if os.name == "nt":
        from adb.adapters.server_process.windows import WindowsAospAdbServerProcessDriver

        selected_driver = WindowsAospAdbServerProcessDriver(
            executable=executable,
            startup_timeout_seconds=startup_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
            probe_interval_seconds=probe_interval_seconds,
        )
    else:
        from adb.adapters.server_process.posix import AospAdbServerProcessDriver

        selected_driver = AospAdbServerProcessDriver(
            executable=executable,
            startup_timeout_seconds=startup_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
            probe_interval_seconds=probe_interval_seconds,
        )

    driver = cast(ResourceDriver[TcpEndpoint, object], selected_driver)
    return AdbServerProcessLifecycle(generation_issuer, driver)


def create_owned_adb_server_runtime(
    *,
    executable: str = "adb",
    startup_timeout_seconds: float = 5.0,
    shutdown_timeout_seconds: float = 5.0,
    probe_interval_seconds: float = 0.05,
    command_policy: AdbServerCommandPolicy = AdbServerCommandPolicy(),
) -> AdbServerRuntime:
    """Compose the default platform-owned ADB server runtime."""

    return create_adb_server_runtime(
        lambda issuer: create_adb_server_process_lifecycle(
            issuer,
            executable=executable,
            startup_timeout_seconds=startup_timeout_seconds,
            shutdown_timeout_seconds=shutdown_timeout_seconds,
            probe_interval_seconds=probe_interval_seconds,
        ),
        command_policy=command_policy,
    )


__all__ = [
    "create_adb_server_process_lifecycle",
    "create_owned_adb_server_runtime",
]
