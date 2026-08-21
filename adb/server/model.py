from __future__ import annotations

from enum import Enum


class AdbServerAvailability(str, Enum):
    """Observed availability of the process-owned ADB server endpoint."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    INDETERMINATE = "indeterminate"


__all__ = ["AdbServerAvailability"]
