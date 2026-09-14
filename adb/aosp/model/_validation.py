from __future__ import annotations

from enum import IntEnum
from numbers import Integral
from typing import TypeVar


OpenEnumT = TypeVar("OpenEnumT", bound=IntEnum)


def require_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    return value


def require_optional_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return require_string(value, field_name=field_name)


def require_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{field_name} must be an integer")
    return int(value)


def normalize_open_enum(
    value: object,
    enum_type: type[OpenEnumT],
    *,
    field_name: str,
) -> OpenEnumT | int:
    """Preserve unknown proto3 enum values as their raw integers."""

    raw = require_int(value, field_name=field_name)
    try:
        return enum_type(raw)
    except ValueError:
        return raw
