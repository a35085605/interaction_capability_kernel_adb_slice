from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import overload

from adb.transport.model import AdbTransport


@dataclass(frozen=True, slots=True, init=False)
class AdbTransportList:
    """Immutable complete transport list observed from one ADB server."""

    transports: tuple[AdbTransport, ...]

    def __init__(self, transports: Iterable[AdbTransport] = ()) -> None:
        if isinstance(transports, AdbTransportList):
            normalized = transports.transports
        else:
            try:
                normalized = tuple(transports)
            except TypeError as exc:
                raise TypeError("transports must be an iterable of AdbTransport values") from exc
        if not all(isinstance(transport, AdbTransport) for transport in normalized):
            raise TypeError("transports must contain only AdbTransport values")
        object.__setattr__(self, "transports", normalized)

    def __iter__(self) -> Iterator[AdbTransport]:
        return iter(self.transports)

    def __len__(self) -> int:
        return len(self.transports)

    @overload
    def __getitem__(self, index: int) -> AdbTransport: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[AdbTransport, ...]: ...

    def __getitem__(self, index: int | slice) -> AdbTransport | tuple[AdbTransport, ...]:
        return self.transports[index]


__all__ = ["AdbTransportList"]
