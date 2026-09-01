from __future__ import annotations

from enum import Enum

from adb.tracking.observation import AdbTrackedTransportObservation
from adb.transport.configuration import AdbConfiguredTransport


class AdbObservedTransportCompatibility(str, Enum):
    """Domain interpretation of one observed transport kind."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNSPECIFIED = "unspecified"


def classify_observed_transport(
    configuration: AdbConfiguredTransport,
    row: AdbTrackedTransportObservation,
) -> AdbObservedTransportCompatibility:
    """Compare one domain transport observation with one configured transport.

    An unspecified observed kind remains compatible fallback evidence. Recognized kinds compare
    directly with the configured domain transport type. Future native kinds are preserved by the
    adapter as unrecognized observations and remain mismatches until explicitly supported.
    """

    if not isinstance(configuration, AdbConfiguredTransport):
        raise TypeError("configuration must be AdbConfiguredTransport")
    if not isinstance(row, AdbTrackedTransportObservation):
        raise TypeError("row must be AdbTrackedTransportObservation")

    observed_kind = row.transport_kind
    if observed_kind.is_unspecified:
        return AdbObservedTransportCompatibility.UNSPECIFIED
    if not observed_kind.is_recognized:
        return AdbObservedTransportCompatibility.MISMATCH

    return (
        AdbObservedTransportCompatibility.MATCH
        if observed_kind.transport_type is configuration.type
        else AdbObservedTransportCompatibility.MISMATCH
    )


__all__ = [
    "AdbObservedTransportCompatibility",
    "classify_observed_transport",
]
