from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
import socket
from typing import Final


if os.name != "nt":
    raise ImportError("windows.tcp_listener is only available on Windows")


_ERROR_INSUFFICIENT_BUFFER: Final = 122
_NO_ERROR: Final = 0
_MIB_TCP_STATE_LISTEN: Final = 2
_TCP_TABLE_OWNER_PID_LISTENER: Final = 3
_MAX_QUERY_ATTEMPTS: Final = 4

_iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


class _MIB_TCPTABLE_OWNER_PID_PREFIX(ctypes.Structure):
    _fields_ = [
        ("dwNumEntries", wintypes.DWORD),
        ("table", _MIB_TCPROW_OWNER_PID * 1),
    ]


_iphlpapi.GetExtendedTcpTable.argtypes = (
    wintypes.LPVOID,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.BOOL,
    wintypes.ULONG,
    ctypes.c_int,
    wintypes.ULONG,
)
_iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD


class WindowsTcpTableError(RuntimeError):
    """Reading the Windows TCP ownership table failed."""


@dataclass(frozen=True, slots=True)
class WindowsTcpListener:
    address: str
    port: int
    pid: int


def _api_error(operation: str, code: int) -> WindowsTcpTableError:
    detail = ctypes.FormatError(code).strip()
    return WindowsTcpTableError(
        f"{operation} failed with Windows error {code}: {detail}"
    )


def _decode_ipv4_address(value: int) -> str:
    # The API stores the address in the same DWORD representation as in_addr.
    # Copy the DWORD's native bytes exactly as inet_ntoa would observe them.
    raw = wintypes.DWORD(value)
    return socket.inet_ntoa(ctypes.string_at(ctypes.byref(raw), 4))


def _decode_port(value: int) -> int:
    return int(socket.ntohs(int(value) & 0xFFFF))


class WindowsTcpListenerTable:
    """Read IPv4 listening endpoints together with their owning PIDs."""

    def read(self) -> tuple[WindowsTcpListener, ...]:
        size = wintypes.DWORD(0)
        buffer: ctypes.Array[ctypes.c_char] | None = None

        for _ in range(_MAX_QUERY_ATTEMPTS):
            table_pointer = (
                None if buffer is None else ctypes.cast(buffer, wintypes.LPVOID)
            )
            result = int(
                _iphlpapi.GetExtendedTcpTable(
                    table_pointer,
                    ctypes.byref(size),
                    False,
                    socket.AF_INET,
                    _TCP_TABLE_OWNER_PID_LISTENER,
                    0,
                )
            )

            if result == _ERROR_INSUFFICIENT_BUFFER:
                if size.value == 0:
                    raise WindowsTcpTableError(
                        "GetExtendedTcpTable requested an empty retry buffer"
                    )
                buffer = ctypes.create_string_buffer(size.value)
                continue

            if result != _NO_ERROR:
                raise _api_error("GetExtendedTcpTable", result)

            if buffer is None:
                return ()

            return self._parse(buffer)

        raise WindowsTcpTableError(
            "GetExtendedTcpTable size changed repeatedly while reading listener ownership"
        )

    @staticmethod
    def _parse(
        buffer: ctypes.Array[ctypes.c_char],
    ) -> tuple[WindowsTcpListener, ...]:
        raw = memoryview(buffer).cast("B")
        if len(raw) < ctypes.sizeof(wintypes.DWORD):
            raise WindowsTcpTableError("TCP listener table was shorter than its header")

        entry_count = int(
            wintypes.DWORD.from_buffer_copy(raw[: ctypes.sizeof(wintypes.DWORD)]).value
        )
        row_size = ctypes.sizeof(_MIB_TCPROW_OWNER_PID)
        row_offset = _MIB_TCPTABLE_OWNER_PID_PREFIX.table.offset
        expected_size = row_offset + entry_count * row_size
        if len(raw) < expected_size:
            raise WindowsTcpTableError(
                "TCP listener table ended before all advertised rows were present"
            )

        listeners: list[WindowsTcpListener] = []
        offset = row_offset
        for _ in range(entry_count):
            row = _MIB_TCPROW_OWNER_PID.from_buffer_copy(raw[offset : offset + row_size])
            offset += row_size

            if int(row.dwState) != _MIB_TCP_STATE_LISTEN:
                continue

            listeners.append(
                WindowsTcpListener(
                    address=_decode_ipv4_address(int(row.dwLocalAddr)),
                    port=_decode_port(int(row.dwLocalPort)),
                    pid=int(row.dwOwningPid),
                )
            )

        return tuple(listeners)


__all__ = [
    "WindowsTcpListener",
    "WindowsTcpListenerTable",
    "WindowsTcpTableError",
]
