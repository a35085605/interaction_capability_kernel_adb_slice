from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from networking import TcpAddress


GenerationT = TypeVar("GenerationT")


@dataclass(frozen=True, slots=True)
class EndpointAccess(Generic[GenerationT]):
    """Usable endpoint access retained by one lifecycle generation."""

    generation: GenerationT
    endpoint: TcpAddress

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")
        if not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress")


@dataclass(frozen=True, slots=True)
class EndpointState(Generic[GenerationT]):
    """Public authority state: current generation plus an optional usable endpoint."""

    generation: GenerationT
    endpoint: TcpAddress | None = None

    def __post_init__(self) -> None:
        if self.generation is None:
            raise TypeError("generation cannot be None")
        if self.endpoint is not None and not isinstance(self.endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")

    @property
    def active(self) -> bool:
        return self.endpoint is not None


__all__ = ["EndpointAccess", "EndpointState"]
