from __future__ import annotations

from dataclasses import dataclass

from adb.server.generation import AdbServerGeneration


@dataclass(frozen=True, slots=True)
class AdbServerActivated:
    """Notification that one server generation transitioned to a usable endpoint.

    The payload deliberately carries only the generation that activated. Consumers must read
    the authoritative backend state before making state-dependent decisions.
    """

    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


@dataclass(frozen=True, slots=True)
class AdbServerDeactivated:
    """Notification that one server generation transitioned away from a usable endpoint.

    ``generation`` identifies the lifetime whose endpoint became unavailable. The backend may
    already expose a newer current generation when a consumer receives this notification.
    """

    generation: AdbServerGeneration

    def __post_init__(self) -> None:
        if not isinstance(self.generation, AdbServerGeneration):
            raise TypeError("generation must be AdbServerGeneration")


__all__ = ["AdbServerActivated", "AdbServerDeactivated"]
