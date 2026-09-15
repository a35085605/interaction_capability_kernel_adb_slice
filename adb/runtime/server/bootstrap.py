from __future__ import annotations

from collections.abc import Callable
from time import monotonic, sleep

from lifecycle.capability.supervision.control import Clock
from adb.runtime.server.mutation import AdbServerCommandPolicy, AdbServerCommands
from adb.runtime.server.runtime import AdbServerRuntime, _snapshot_reader
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.lifecycle import AdbServerLifecycle, AdbServerLifecycleFactory


_Sleeper = Callable[[float], None]


def create_adb_server_runtime(
    lifecycle_factory: AdbServerLifecycleFactory,
    *,
    command_policy: AdbServerCommandPolicy = AdbServerCommandPolicy(),
    _sleeper: _Sleeper = sleep,
    _clock: Clock = monotonic,
) -> AdbServerRuntime:
    """Build a fresh ADB server Runtime scope without activating the server."""

    if not callable(lifecycle_factory):
        raise TypeError("lifecycle_factory must be callable")
    if not isinstance(command_policy, AdbServerCommandPolicy):
        raise TypeError("command_policy must be AdbServerCommandPolicy")

    generation_issuer = AdbServerGenerationIssuer()
    lifecycle = lifecycle_factory(generation_issuer)
    if not isinstance(lifecycle, AdbServerLifecycle):
        raise TypeError("lifecycle_factory must return an AdbServerLifecycle")

    return AdbServerRuntime(
        snapshot=_snapshot_reader(lifecycle),
        mutations=AdbServerCommands(
            lifecycle,
            policy=command_policy,
            _sleeper=_sleeper,
            _clock=_clock,
        ),
    )


# Compatibility name retained for callers of the original API.
bootstrap_adb_server_runtime = create_adb_server_runtime


__all__ = ["bootstrap_adb_server_runtime", "create_adb_server_runtime"]
