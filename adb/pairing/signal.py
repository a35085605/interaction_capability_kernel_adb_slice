from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.pairing.command import AdbWirelessPair
from native_attempt import NativeAttemptResult


AdbPairingCommandOperation: TypeAlias = AdbWirelessPair


@dataclass(frozen=True, slots=True)
class AdbPairingCommandCompleted:
    """Signal carrying the result of one atomic ADB pairing command attempt."""

    operation: AdbPairingCommandOperation
    result: NativeAttemptResult

    def __post_init__(self) -> None:
        if not isinstance(self.operation, AdbWirelessPair):
            raise TypeError("operation must be an ADB pairing command operation")
        if not isinstance(self.result, NativeAttemptResult):
            raise TypeError("result must be NativeAttemptResult")


AdbPairingSignal: TypeAlias = AdbPairingCommandCompleted


__all__ = [
    "AdbPairingCommandCompleted",
    "AdbPairingCommandOperation",
    "AdbPairingSignal",
]
