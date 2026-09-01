from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral

from adb.transport.configuration import AdbTransportType
from adb.transport.identity import AdbDeviceSerial, AdbTransportId


@dataclass(frozen=True, slots=True)
class AdbObservedTransportKind:
    """Open domain value describing an observed ADB transport kind.

    A recognized observation carries ``transport_type``. An explicitly unspecified
    observation carries neither field. A future native kind that this library does not yet
    understand carries only ``native_code`` so forward-compatible evidence is not collapsed
    into the unspecified case.
    """

    transport_type: AdbTransportType | None = None
    native_code: int | None = None

    def __post_init__(self) -> None:
        if self.transport_type is not None and not isinstance(
            self.transport_type, AdbTransportType
        ):
            raise TypeError("transport_type must be AdbTransportType or None")
        if self.native_code is not None:
            if isinstance(self.native_code, bool) or not isinstance(self.native_code, Integral):
                raise TypeError("native_code must be an integer or None")
            object.__setattr__(self, "native_code", int(self.native_code))
        if self.transport_type is not None and self.native_code is not None:
            raise ValueError("recognized observed transport kind cannot carry native_code")

    @classmethod
    def recognized(cls, transport_type: AdbTransportType) -> "AdbObservedTransportKind":
        return cls(transport_type=transport_type)

    @classmethod
    def unspecified(cls) -> "AdbObservedTransportKind":
        return cls()

    @classmethod
    def unrecognized(cls, native_code: int) -> "AdbObservedTransportKind":
        return cls(native_code=native_code)

    @property
    def is_recognized(self) -> bool:
        return self.transport_type is not None

    @property
    def is_unspecified(self) -> bool:
        return self.transport_type is None and self.native_code is None

    @property
    def is_unrecognized(self) -> bool:
        return self.native_code is not None


@dataclass(frozen=True, slots=True)
class AdbTrackedTransportObservation:
    """Domain observation of one transport reported by an ADB server.

    ``serial_text`` preserves the observed serial text exactly. It is intentionally not an
    ``AdbDeviceSerial`` because native observations may omit or otherwise provide a value that
    does not satisfy the stable domain identity invariant. ``transport_id`` is present only when
    the native observation contains a valid positive server-local transport identity.
    """

    serial_text: str
    transport_kind: AdbObservedTransportKind
    transport_id: AdbTransportId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.serial_text, str):
            raise TypeError("serial_text must be a string")
        if not isinstance(self.transport_kind, AdbObservedTransportKind):
            raise TypeError("transport_kind must be AdbObservedTransportKind")
        if self.transport_id is not None and not isinstance(self.transport_id, AdbTransportId):
            raise TypeError("transport_id must be AdbTransportId or None")

    def matches_serial(self, serial: AdbDeviceSerial) -> bool:
        """Whether this observation exactly matches one stable serial identity."""

        if not isinstance(serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")
        return self.serial_text == serial.value


__all__ = ["AdbObservedTransportKind", "AdbTrackedTransportObservation"]
