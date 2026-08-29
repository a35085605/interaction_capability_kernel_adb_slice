from __future__ import annotations

from numbers import Integral


_TRANSPORT_BY_SERIAL_PREFIX = "host:transport:"
_TRANSPORT_BY_ID_PREFIX = "host:transport-id:"


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
    """Build the native smart-socket service selecting one serial."""

    return _TRANSPORT_BY_SERIAL_PREFIX + _require_service_text(
        serial,
        field_name="ADB transport serial",
    )


def transport_by_id_service(transport_id: int) -> str:
    """Build the native smart-socket service selecting one transport id."""

    return f"{_TRANSPORT_BY_ID_PREFIX}{_require_transport_id(transport_id)}"


def transport_features_by_serial_service(serial: str) -> str:
    """Build the native host query for one serial-selected transport feature list."""

    normalized = _require_service_text(serial, field_name="ADB transport serial")
    return f"host-serial:{normalized}:features"


def transport_features_by_id_service(transport_id: int) -> str:
    """Build the native host query for one id-selected transport feature list."""

    return f"host-transport-id:{_require_transport_id(transport_id)}:features"


def is_transport_selection_service(service: str) -> bool:
    """Whether a service string is one of the native transport-selection services."""

    if not isinstance(service, str):
        return False
    return service.startswith((_TRANSPORT_BY_SERIAL_PREFIX, _TRANSPORT_BY_ID_PREFIX))


__all__ = [
    "is_transport_selection_service",
    "transport_by_id_service",
    "transport_by_serial_service",
    "transport_features_by_id_service",
    "transport_features_by_serial_service",
]
