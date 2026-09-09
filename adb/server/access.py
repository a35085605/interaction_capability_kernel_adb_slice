from __future__ import annotations

from dataclasses import dataclass

from adb._lifecycle.endpoint import EndpointAccess


@dataclass(frozen=True, slots=True)
class AdbServerAccess(EndpointAccess):
    """Usable ADB server endpoint access information."""


__all__ = ["AdbServerAccess"]
