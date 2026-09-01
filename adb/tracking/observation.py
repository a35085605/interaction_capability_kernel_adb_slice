from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from numbers import Integral

from adb.transport.configuration import AdbTransportType
from adb.transport.identity import AdbDeviceSerial, AdbTransportId


class AdbTransportState(str, Enum):
    """Domain transport states that are meaningful to tracking consumers."""

    CONNECTING = "connecting"
    AUTHORIZING = "authorizing"
    UNAUTHORIZED = "unauthorized"
    NO_PERMISSION = "no_permission"
    DETACHED = "detached"
    OFFLINE = "offline"
    BOOTLOADER = "bootloader"
    READY = "ready"
    HOST = "host"
    RECOVERY = "recovery"
    SIDELOAD = "sideload"
    RESCUE = "rescue"


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
class AdbObservedTransportState:
    """Open domain value describing an observed ADB transport state.

    Native AOSP ``ANY`` is represented as unspecified. Known states are translated to
    ``AdbTransportState``. Future native state values retain only their numeric code so protocol
    evolution does not leak native enums into domain policy.
    """

    transport_state: AdbTransportState | None = None
    native_code: int | None = None

    def __post_init__(self) -> None:
        if self.transport_state is not None and not isinstance(
            self.transport_state, AdbTransportState
        ):
            raise TypeError("transport_state must be AdbTransportState or None")
        if self.native_code is not None:
            if isinstance(self.native_code, bool) or not isinstance(self.native_code, Integral):
                raise TypeError("native_code must be an integer or None")
            object.__setattr__(self, "native_code", int(self.native_code))
        if self.transport_state is not None and self.native_code is not None:
            raise ValueError("recognized observed transport state cannot carry native_code")

    @classmethod
    def recognized(cls, transport_state: AdbTransportState) -> "AdbObservedTransportState":
        return cls(transport_state=transport_state)

    @classmethod
    def unspecified(cls) -> "AdbObservedTransportState":
        return cls()

    @classmethod
    def unrecognized(cls, native_code: int) -> "AdbObservedTransportState":
        return cls(native_code=native_code)

    @property
    def is_recognized(self) -> bool:
        return self.transport_state is not None

    @property
    def is_unspecified(self) -> bool:
        return self.transport_state is None and self.native_code is None

    @property
    def is_unrecognized(self) -> bool:
        return self.native_code is not None


@dataclass(frozen=True, slots=True)
class AdbTrackedTransportObservation:
    """Domain observation of one transport reported by an ADB server.

    ``serial_text`` preserves the observed serial text exactly. It is intentionally not an
    ``AdbDeviceSerial`` because native observations may omit or otherwise provide a value that
    does not satisfy the stable domain identity invariant. Native transport kind and state values
    cross the protocol boundary before this value is created; unknown native values remain
    explicit open-domain evidence. ``transport_id`` is present only when the native observation
    contains a valid positive server-local transport identity.
    """

    serial_text: str
    transport_kind: AdbObservedTransportKind
    transport_id: AdbTransportId | None = None
    state: AdbObservedTransportState = field(default_factory=AdbObservedTransportState.unspecified)

    def __post_init__(self) -> None:
        if not isinstance(self.serial_text, str):
            raise TypeError("serial_text must be a string")
        if not isinstance(self.transport_kind, AdbObservedTransportKind):
            raise TypeError("transport_kind must be AdbObservedTransportKind")
        if self.transport_id is not None and not isinstance(self.transport_id, AdbTransportId):
            raise TypeError("transport_id must be AdbTransportId or None")
        if not isinstance(self.state, AdbObservedTransportState):
            raise TypeError("state must be AdbObservedTransportState")

    def matches_serial(self, serial: AdbDeviceSerial) -> bool:
        """Whether this observation exactly matches one stable serial identity."""

        if not isinstance(serial, AdbDeviceSerial):
            raise TypeError("serial must be AdbDeviceSerial")
        return self.serial_text == serial.value


__all__ = [
    "AdbObservedTransportKind",
    "AdbObservedTransportState",
    "AdbTrackedTransportObservation",
    "AdbTransportState",
]
