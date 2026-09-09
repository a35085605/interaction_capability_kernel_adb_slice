from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from networking import TcpAddress

from adb._lifecycle.snapshot import LifecycleSnapshot


GenerationT = TypeVar("GenerationT")
EndpointAccessT = TypeVar("EndpointAccessT", bound="EndpointAccess")


@dataclass(frozen=True, slots=True)
class EndpointAccess:
    """Usable endpoint access information, independent of lifecycle authority generation."""

    endpoint: TcpAddress

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class EndpointState(
    LifecycleSnapshot[GenerationT, EndpointAccessT | None],
    Generic[GenerationT, EndpointAccessT],
):
    """Public authority state: current generation plus optional endpoint access."""

    access: EndpointAccessT | None = None

    def __post_init__(self) -> None:
        LifecycleSnapshot.__post_init__(self)
        if self.access is not None and not isinstance(self.access, EndpointAccess):
            raise TypeError("access must be EndpointAccess or None")

    @property
    def endpoint(self) -> TcpAddress | None:
        return None if self.access is None else self.access.endpoint

    @property
    def active(self) -> bool:
        return self.access is not None


__all__ = ["EndpointAccess", "EndpointState"]
