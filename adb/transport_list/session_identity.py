from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterator

from adb.epoch import Epoch, EpochSequence


class AdbTransportListSessionEpoch(Epoch):
    """Monotonic ordinal for one transport-list watch session within an issuer scope."""

    __slots__ = ()


class _AdbTransportListSessionScope:
    """Opaque revocable namespace shared by one server-lifetime session issuer."""

    __slots__ = ("lock", "active")

    def __init__(self) -> None:
        self.lock = Lock()
        self.active = True


@dataclass(frozen=True, slots=True)
class AdbTransportListSessionIdentity:
    """Opaque identity for one authoritative transport-list producer session.

    A fresh identity is issued for every session rebuild, even while the ADB server endpoint and
    server lifetime remain unchanged. The identity carries no ``ServerIdentity`` provenance.
    Its issuer scope is revocable so retirement can fence identities that were issued before the
    transport-list state transition became visible.
    """

    _scope: _AdbTransportListSessionScope = field(repr=False)
    epoch: AdbTransportListSessionEpoch

    def __post_init__(self) -> None:
        if not isinstance(self._scope, _AdbTransportListSessionScope):
            raise TypeError("_scope must be _AdbTransportListSessionScope")
        if not isinstance(self.epoch, AdbTransportListSessionEpoch):
            raise TypeError("epoch must be AdbTransportListSessionEpoch")

    @contextmanager
    def _authority_guard(self) -> Iterator[bool]:
        """Hold the issuer-scope fence while an authoritative state transition commits."""

        with self._scope.lock:
            yield self._scope.active

    def __str__(self) -> str:
        return str(self.epoch)


class AdbTransportListSessionIdentityIssuer:
    """Issue session identities inside one revocable server-lifetime admission scope."""

    __slots__ = ("_scope", "_sequence")

    def __init__(self) -> None:
        self._scope = _AdbTransportListSessionScope()
        self._sequence = EpochSequence(AdbTransportListSessionEpoch)

    @property
    def active(self) -> bool:
        """Whether this issuer may still admit new transport-list sessions."""

        with self._scope.lock:
            return self._scope.active

    def issue(self) -> AdbTransportListSessionIdentity | None:
        """Issue a fresh identity, or return ``None`` after the issuer has been revoked."""

        with self._scope.lock:
            if not self._scope.active:
                return None
            return AdbTransportListSessionIdentity(
                self._scope,
                self._sequence.issue(),
            )

    def _revoke(self) -> bool:
        """Close this issuer scope; only the owning transport-list authority calls this."""

        with self._scope.lock:
            if not self._scope.active:
                return False
            self._scope.active = False
            return True

    def owns(self, identity: AdbTransportListSessionIdentity) -> bool:
        """Whether ``identity`` was issued from this issuer scope."""

        if not isinstance(identity, AdbTransportListSessionIdentity):
            raise TypeError("identity must be AdbTransportListSessionIdentity")
        return identity._scope is self._scope


__all__ = [
    "AdbTransportListSessionEpoch",
    "AdbTransportListSessionIdentity",
    "AdbTransportListSessionIdentityIssuer",
]
