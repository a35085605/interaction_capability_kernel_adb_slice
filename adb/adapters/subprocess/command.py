from __future__ import annotations

import math


def normalize_executable(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("ADB executable must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("ADB executable cannot be empty")
    return normalized


def normalize_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("ADB subprocess timeout must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError("ADB subprocess timeout must be finite and greater than zero")
    return normalized


__all__ = ["normalize_executable", "normalize_timeout"]
