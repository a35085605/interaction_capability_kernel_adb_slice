from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Real
import socket
import struct
from typing import Callable

from adb.errors import (
    AdbProtocolError,
    AdbServerConnectionError,
    AdbServiceError,
    AdbTimeoutError,
    AdbTransportAmbiguousError,
    AdbTransportNotFoundError,
    AdbTransportUnavailableError,
)
from adb._internal.framing import encode_service, parse_hex_length
from adb.server.endpoint import AdbServerEndpoint
from adb.transport.selection import (
    AdbTransportById,
    AdbTransportBySerial,
    AdbTransportSelector,
)


_SHELL_STDOUT = 1
_SHELL_STDERR = 2
_SHELL_EXIT = 3


def _normalize_timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("ADB timeout must be a real number")
    timeout = float(value)
    if not isfinite(timeout) or timeout <= 0:
        raise ValueError("ADB timeout must be finite and greater than zero")
    return timeout


def _transport_service(selector: AdbTransportSelector) -> str:
    if isinstance(selector, AdbTransportBySerial):
        return f"host:transport:{selector.serial.value}"
    if isinstance(selector, AdbTransportById):
        return f"host:transport-id:{selector.transport_id.value}"
    raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")


def _feature_service(selector: AdbTransportSelector) -> str:
    if isinstance(selector, AdbTransportBySerial):
        return f"host-serial:{selector.serial.value}:features"
    if isinstance(selector, AdbTransportById):
        return f"host-transport-id:{selector.transport_id.value}:features"
    raise TypeError("selector must be AdbTransportBySerial or AdbTransportById")


@dataclass(frozen=True, slots=True)
class ShellV2Result:
    stdout: bytes
    stderr: bytes
    exit_code: int


class AdbServiceClient:
    """Private smart-socket client used by typed kernel adapters.

    This class deliberately stays under ``adb._internal``. Public kernel capabilities
    expose typed inspectors and commands rather than an unrestricted shell executor.
    """

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        timeout_seconds: float = 5.0,
        *,
        _socket_factory: Callable[..., socket.socket] = socket.create_connection,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        self.endpoint = endpoint
        self.timeout_seconds = _normalize_timeout(timeout_seconds)
        self._socket_factory = _socket_factory

    def host_query(self, service: str) -> bytes:
        """Run one length-prefixed host query and return its payload."""

        sock = self._connect()
        try:
            self._request(sock, service)
            return self._read_protocol_string(sock, context=service)
        finally:
            self._close(sock)

    def first_stream_frame(self, service: str) -> bytes:
        """Read the first length-prefixed frame from a host streaming service."""

        sock = self._connect()
        try:
            self._request(sock, service)
            return self._read_protocol_string(sock, context=service)
        finally:
            self._close(sock)

    def features(self, selector: AdbTransportSelector) -> frozenset[str]:
        payload = self.host_query(_feature_service(selector))
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AdbProtocolError("ADB feature list is not valid UTF-8") from exc
        return frozenset(part for part in text.split(",") if part)

    def raw_exec(self, selector: AdbTransportSelector, command: str) -> bytes:
        """Private raw ``exec:`` primitive for fixed typed adapter commands."""

        sock = self._connect()
        try:
            self._select_transport(sock, selector)
            self._request(sock, f"exec:{command}")
            return self._read_until_eof(sock)
        finally:
            self._close(sock)

    def shell_v2(self, selector: AdbTransportSelector, command: str) -> ShellV2Result:
        """Private shell-v2 primitive with stdout/stderr/exit-code separation."""

        sock = self._connect()
        try:
            self._select_transport(sock, selector)
            self._request(sock, f"shell,v2,raw:{command}")
            stdout = bytearray()
            stderr = bytearray()
            exit_code: int | None = None

            while True:
                first = self._recv_up_to(sock, 1)
                if not first:
                    break
                header = first + self._recv_exact(sock, 4)
                packet_id = header[0]
                size = struct.unpack("<I", header[1:])[0]
                payload = self._recv_exact(sock, size)
                if packet_id == _SHELL_STDOUT:
                    stdout.extend(payload)
                elif packet_id == _SHELL_STDERR:
                    stderr.extend(payload)
                elif packet_id == _SHELL_EXIT:
                    if len(payload) != 1:
                        raise AdbProtocolError("ADB shell-v2 exit packet must contain one byte")
                    if exit_code is not None:
                        raise AdbProtocolError("ADB shell-v2 emitted multiple exit packets")
                    exit_code = payload[0]

            if exit_code is None:
                raise AdbProtocolError("ADB shell-v2 stream ended without an exit packet")
            return ShellV2Result(bytes(stdout), bytes(stderr), exit_code)
        finally:
            self._close(sock)

    def _connect(self) -> socket.socket:
        try:
            sock = self._socket_factory(
                (self.endpoint.host, self.endpoint.port),
                timeout=self.timeout_seconds,
            )
            sock.settimeout(self.timeout_seconds)
            return sock
        except socket.timeout as exc:
            raise AdbTimeoutError("timed out connecting to the ADB server") from exc
        except OSError as exc:
            raise AdbServerConnectionError(
                f"failed to connect to ADB server {self.endpoint.host}:{self.endpoint.port}: {exc}"
            ) from exc

    def _select_transport(self, sock: socket.socket, selector: AdbTransportSelector) -> None:
        self._request(sock, _transport_service(selector), transport_selection=True)

    def _request(
        self,
        sock: socket.socket,
        service: str,
        *,
        transport_selection: bool = False,
    ) -> None:
        try:
            sock.sendall(encode_service(service))
        except socket.timeout as exc:
            raise AdbTimeoutError(f"timed out sending ADB service {service!r}") from exc
        except OSError as exc:
            raise AdbServerConnectionError(
                f"failed to send ADB service {service!r}: {exc}"
            ) from exc

        status = self._recv_exact(sock, 4)
        if status == b"OKAY":
            return
        if status != b"FAIL":
            raise AdbProtocolError(f"unexpected ADB service status: {status!r}")
        detail_raw = self._read_protocol_string(sock, context="service error")
        try:
            detail = detail_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AdbProtocolError("ADB service error is not valid UTF-8") from exc
        if transport_selection:
            self._raise_transport_error(service, detail)
        raise AdbServiceError(service, detail or "request rejected")

    def _raise_transport_error(self, service: str, detail: str) -> None:
        lowered = detail.lower()
        if "more than one" in lowered or "multiple devices" in lowered:
            raise AdbTransportAmbiguousError(service, detail)
        if (
            "not found" in lowered
            or "no devices" in lowered
            or "no device" in lowered
            or "unknown transport" in lowered
        ):
            raise AdbTransportNotFoundError(service, detail)
        if (
            "offline" in lowered
            or "unauthorized" in lowered
            or "no permissions" in lowered
            or "permission" in lowered
        ):
            raise AdbTransportUnavailableError(service, detail)
        raise AdbTransportUnavailableError(service, detail or "transport unavailable")

    def _read_protocol_string(self, sock: socket.socket, *, context: str) -> bytes:
        length = parse_hex_length(self._recv_exact(sock, 4), context=context)
        return self._recv_exact(sock, length)

    def _recv_exact(self, sock: socket.socket, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self._recv_up_to(sock, remaining)
            if not chunk:
                raise AdbServerConnectionError("unexpected EOF from ADB smart-socket")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv_up_to(self, sock: socket.socket, size: int) -> bytes:
        try:
            return sock.recv(size)
        except socket.timeout as exc:
            raise AdbTimeoutError("ADB smart-socket read timed out") from exc
        except OSError as exc:
            raise AdbServerConnectionError(f"ADB smart-socket read failed: {exc}") from exc

    def _read_until_eof(self, sock: socket.socket) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = self._recv_up_to(sock, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    @staticmethod
    def _close(sock: socket.socket) -> None:
        try:
            sock.close()
        except OSError:
            pass


__all__ = ["AdbServiceClient", "ShellV2Result"]
