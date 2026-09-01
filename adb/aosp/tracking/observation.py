"""Compatibility imports for relocated AOSP tracking translation adapters."""

from adb.adapters.aosp.tracking import (
    to_tracked_transport_observation,
    to_tracked_transport_observations,
)

__all__ = [
    "to_tracked_transport_observation",
    "to_tracked_transport_observations",
]
