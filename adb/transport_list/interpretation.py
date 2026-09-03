from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adb.transport.configuration import AdbConfiguredTransport
    from adb.transport.model import AdbTransport


class AdbObservedTransportCompatibility(str, Enum):
    """Domain interpretation of one observed transport kind."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNSPECIFIED = "unspecified"


def classify_observed_transport(
    configuration: AdbConfiguredTransport,
    transport: AdbTransport,
) -> AdbObservedTransportCompatibility:
    """Classify one observed transport by recognized type match, unspecified fallback, or
    unrecognized native-kind mismatch.
    """

    from adb.transport.configuration import AdbConfiguredTransport
    from adb.transport.model import AdbTransport

    if not isinstance(configuration, AdbConfiguredTransport):
        raise TypeError("configuration must be AdbConfiguredTransport")
    if not isinstance(transport, AdbTransport):
        raise TypeError("transport must be AdbTransport")

    observed_kind = transport.transport_kind
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
