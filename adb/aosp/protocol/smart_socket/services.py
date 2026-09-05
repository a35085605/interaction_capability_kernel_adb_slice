from __future__ import annotations

from numbers import Integral


_TRANSPORT_BY_SERIAL_PREFIX = "host:transport:"
_TRANSPORT_BY_ID_PREFIX = "host:transport-id:"
TRACK_DEVICES_PROTO_BINARY_SERVICE = "host:track-devices-proto-binary"


def _require_service_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} cannot be empty")
    return value


def _require_transport_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError("ADB transport id service value must be an integer")
    return int(value)


def transport_by_serial_service(serial: str) -> str:
    """Build a smart-socket service that selects a transport by serial."""

    return _TRANSPORT_BY_SERIAL_PREFIX + _require_service_text(
        serial,
        field_name="ADB transport serial",
    )


def transport_by_id_service(transport_id: int) -> str:
    """Build a smart-socket service that selects a transport by ID."""

    return f"{_TRANSPORT_BY_ID_PREFIX}{_require_transport_id(transport_id)}"


def transport_features_by_serial_service(serial: str) -> str:
    """Build a host query for features of a serial-selected transport."""

    normalized = _require_service_text(serial, field_name="ADB transport serial")
    return f"host-serial:{normalized}:features"


def transport_features_by_id_service(transport_id: int) -> str:
    """Build a host query for features of an ID-selected transport."""

    return f"host-transport-id:{_require_transport_id(transport_id)}:features"


def is_transport_selection_service(service: str) -> bool:
    """Return whether a service string selects an ADB transport."""

    if not isinstance(service, str):
        return False
    return service.startswith((_TRANSPORT_BY_SERIAL_PREFIX, _TRANSPORT_BY_ID_PREFIX))


__all__ = [
    "TRACK_DEVICES_PROTO_BINARY_SERVICE",
    "is_transport_selection_service",
    "transport_by_id_service",
    "transport_by_serial_service",
    "transport_features_by_id_service",
    "transport_features_by_serial_service",
]
