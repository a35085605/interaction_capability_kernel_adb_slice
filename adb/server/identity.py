from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from adb.server.model import AdbServerEndpoint


@dataclass(frozen=True, slots=True, order=True)
class AdbServerIncarnationId:
    """Coordinator-local monotonic fencing identity for an ADB server lifetime epoch.

    This identifier is allocated by one local coordination domain. It is not an ADB protocol
    identifier, OS process identifier, or globally verifiable native-lifetime identity.
    """

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise TypeError("ADB server incarnation id must be an integer")
        if self.value <= 0:
            raise ValueError("ADB server incarnation id must be greater than zero")


@dataclass(frozen=True, slots=True)
class AdbServerIncarnation:
    """Coordinator-local reference to one ADB server lifetime epoch.

    ``endpoint`` answers where the service was reached for this epoch. ``id`` is only a local
    monotonic fencing token used to reject delayed work and stale events. The value does not by
    itself prove that two endpoint observations refer to the same native process; exact native
    lifetime authority is represented separately by ``AdbOwnedServer`` and its private handle.
    """

    endpoint: AdbServerEndpoint
    id: AdbServerIncarnationId

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(self.id, AdbServerIncarnationId):
            raise TypeError("id must be AdbServerIncarnationId")

    @property
    def generation(self) -> int:
        """Compatibility projection of the coordinator-local incarnation id."""

        return self.id.value


class _AdbServerIncarnationSequence:
    """Mint monotonic incarnation ids for one local coordination domain."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._latest_value = 0

    def next(self, endpoint: AdbServerEndpoint) -> AdbServerIncarnation:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        with self._lock:
            self._latest_value += 1
            return AdbServerIncarnation(
                endpoint,
                AdbServerIncarnationId(self._latest_value),
            )

    @property
    def latest_id(self) -> AdbServerIncarnationId | None:
        with self._lock:
            if self._latest_value == 0:
                return None
            return AdbServerIncarnationId(self._latest_value)


__all__ = ["AdbServerIncarnation", "AdbServerIncarnationId"]
