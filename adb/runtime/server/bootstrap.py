from __future__ import annotations

from adb.runtime.server.mutation import AdbServerMutationFacade
from adb.runtime.server.runtime import AdbServerRuntime, _snapshot_reader
from adb.server.generation import AdbServerGenerationIssuer
from adb.server.lifecycle import AdbServerLifecycle, AdbServerLifecycleFactory


def bootstrap_adb_server_runtime(
    lifecycle_factory: AdbServerLifecycleFactory,
) -> AdbServerRuntime:
    """Build a fresh ADB server Runtime scope without activating the server."""

    if not callable(lifecycle_factory):
        raise TypeError("lifecycle_factory must be callable")

    generation_issuer = AdbServerGenerationIssuer()
    lifecycle = lifecycle_factory(generation_issuer)
    if not isinstance(lifecycle, AdbServerLifecycle):
        raise TypeError("lifecycle_factory must return an AdbServerLifecycle")

    return AdbServerRuntime(
        snapshot=_snapshot_reader(lifecycle),
        mutations=AdbServerMutationFacade(lifecycle),
    )


__all__ = ["bootstrap_adb_server_runtime"]
