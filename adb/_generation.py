from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from adb.epoch import Epoch, EpochSequence


GenerationT = TypeVar("GenerationT")
EpochT = TypeVar("EpochT", bound=Epoch)


class EpochBackedGenerationIssuer(Generic[GenerationT, EpochT]):
    """Issue domain generation values backed by a monotonic epoch sequence.

    The issuer owns only the sequencing and resume-after mechanics. Domain-specific
    generation identity and semantics remain in the generation type produced by
    ``generation_factory``.
    """

    __slots__ = ("_generation_factory", "_sequence")

    def __init__(
        self,
        epoch_type: type[EpochT],
        generation_factory: Callable[[EpochT], GenerationT],
        *,
        after_epoch: EpochT | None = None,
    ) -> None:
        if not callable(generation_factory):
            raise TypeError("generation_factory must be callable")
        if after_epoch is not None and not isinstance(after_epoch, epoch_type):
            raise TypeError("after_epoch must match epoch_type or be None")
        initial_value = 0 if after_epoch is None else after_epoch.value
        self._sequence = EpochSequence(epoch_type, initial_value=initial_value)
        self._generation_factory = generation_factory

    def issue(self) -> GenerationT:
        """Issue the next epoch and project it into the domain generation type."""

        return self._generation_factory(self._sequence.issue())


__all__ = ["EpochBackedGenerationIssuer"]
