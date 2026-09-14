from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


ResourceT = TypeVar("ResourceT")


def cleanup_reverse(
    resources: tuple[ResourceT, ...],
    close_one: Callable[[ResourceT], None],
) -> None:
    """Clean resources in reverse order and re-raise the first cleanup failure.

    Every resource is offered to ``close_one`` even when an earlier cleanup attempt
    fails. This keeps retryable ownership semantics deterministic while preserving
    the first failure as the primary diagnostic.
    """

    if not isinstance(resources, tuple):
        raise TypeError("resources must be a PhysicalResources tuple")
    if not callable(close_one):
        raise TypeError("close_one must be callable")

    first_error: BaseException | None = None
    for resource in reversed(resources):
        try:
            close_one(resource)
        except BaseException as exc:
            if first_error is None:
                first_error = exc

    if first_error is not None:
        raise first_error


__all__ = ["cleanup_reverse"]
