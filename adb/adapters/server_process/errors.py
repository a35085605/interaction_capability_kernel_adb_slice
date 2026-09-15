from __future__ import annotations


class AospAdbServerStartError(RuntimeError):
    """Infrastructure failure while creating or establishing an owned ADB server."""


class AospAdbServerTerminationUnconfirmed(RuntimeError):
    """Failure to confirm termination of an owned ADB server process."""


__all__ = ["AospAdbServerStartError", "AospAdbServerTerminationUnconfirmed"]
