from __future__ import annotations

from enum import Enum

from adb.aosp.tracking.model import ConnectionType, Device
from adb.transport.configuration import AdbConfiguredTransport, AdbTransportType


class AdbObservedTransportCompatibility(str, Enum):
    """Domain interpretation of one AOSP connection-type observation."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNSPECIFIED = "unspecified"


def classify_observed_transport(
    configuration: AdbConfiguredTransport,
    row: Device,
) -> AdbObservedTransportCompatibility:
    """Compare raw AOSP connection-type evidence with one domain transport configuration.

    AOSP ``UNKNOWN`` means the observation does not specify a transport kind and therefore remains
    compatible fallback evidence. Known AOSP USB/SOCKET values map to domain USB/TCP. Future AOSP
    values remain distinct from ``UNKNOWN`` and are treated as a type mismatch until explicitly
    supported.
    """

    if not isinstance(configuration, AdbConfiguredTransport):
        raise TypeError("configuration must be AdbConfiguredTransport")
    if not isinstance(row, Device):
        raise TypeError("row must be AOSP Device")

    connection_type = row.connection_type
    if connection_type is ConnectionType.UNKNOWN:
        return AdbObservedTransportCompatibility.UNSPECIFIED
    if connection_type is ConnectionType.USB:
        observed_type = AdbTransportType.USB
    elif connection_type is ConnectionType.SOCKET:
        observed_type = AdbTransportType.TCP
    else:
        return AdbObservedTransportCompatibility.MISMATCH

    return (
        AdbObservedTransportCompatibility.MATCH
        if observed_type is configuration.type
        else AdbObservedTransportCompatibility.MISMATCH
    )


__all__ = [
    "AdbObservedTransportCompatibility",
    "classify_observed_transport",
]
