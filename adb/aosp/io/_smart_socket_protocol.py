from __future__ import annotations

from collections.abc import Callable

from adb.aosp.protocol.smart_socket.framing import encode_service, parse_hex_length
from adb.errors import AdbProtocolError, AdbServerConnectionError, AdbServiceError


Receive = Callable[[int], bytes]
ReadExact = Callable[[int], bytes]
SendAll = Callable[[bytes], None]


def recv_exact(recv: Receive, size: int, *, eof_message: str) -> bytes:
    """Read exactly ``size`` bytes using a caller-owned receive operation."""

    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = recv(remaining)
        if not chunk:
            raise AdbServerConnectionError(eof_message)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_length_prefixed(read_exact: ReadExact, *, context: str) -> bytes:
    """Read one ADB four-hex-digit length-prefixed payload."""

    length = parse_hex_length(read_exact(4), context=context)
    return read_exact(length)


def send_service_request(sendall: SendAll, service: str) -> None:
    """Encode and send one ADB smart-socket service request."""

    sendall(encode_service(service))


def read_service_response(
    service: str,
    read_exact: ReadExact,
    *,
    rejection_detail: str = "request rejected",
) -> None:
    """Read and validate the ADB ``OKAY``/``FAIL`` response for one service request."""

    status = read_exact(4)
    if status == b"OKAY":
        return
    if status != b"FAIL":
        raise AdbProtocolError(f"unexpected ADB service status: {status!r}")

    detail_raw = read_length_prefixed(read_exact, context="service error")
    try:
        detail = detail_raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError("ADB service error is not valid UTF-8") from exc
    raise AdbServiceError(service, detail or rejection_detail)


__all__ = [
    "read_length_prefixed",
    "read_service_response",
    "recv_exact",
    "send_service_request",
]
