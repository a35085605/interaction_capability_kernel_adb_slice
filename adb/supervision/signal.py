"""Compatibility exports for configured-transport supervision signals."""

from typing import TypeAlias

from adb.transport.lifecycle.supervision.signal import (
    AdbConfiguredTransportRecoveryExhausted,
    AdbConfiguredTransportResolutionChanged,
    AdbConfiguredTransportSupervisionSignal,
)

AdbSupervisionSignal: TypeAlias = AdbConfiguredTransportSupervisionSignal

__all__ = [
    "AdbConfiguredTransportRecoveryExhausted",
    "AdbConfiguredTransportResolutionChanged",
    "AdbSupervisionSignal",
]
