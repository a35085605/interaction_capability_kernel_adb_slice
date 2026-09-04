from __future__ import annotations

from adb.errors import AdbProtocolError


class ProtoReader:
    """Minimal protobuf wire reader."""

    def __init__(self, payload: bytes) -> None:
        if not isinstance(payload, bytes):
            raise TypeError("protobuf payload must be bytes")
        self.payload = payload
        self.offset = 0

    @property
    def done(self) -> bool:
        return self.offset == len(self.payload)

    def read_varint(self) -> int:
        result = 0
        for byte_index in range(10):
            if self.offset >= len(self.payload):
                raise AdbProtocolError("truncated protobuf varint")
            byte = self.payload[self.offset]
            self.offset += 1
            if byte_index == 9 and byte > 1:
                raise AdbProtocolError("protobuf varint exceeds 64 bits")
            result |= (byte & 0x7F) << (7 * byte_index)
            if byte < 0x80:
                return result
        raise AdbProtocolError("protobuf varint exceeds 64 bits")

    def read_key(self) -> tuple[int, int]:
        key = self.read_varint()
        field_number = key >> 3
        wire_type = key & 0x07
        if field_number == 0:
            raise AdbProtocolError("protobuf field number cannot be zero")
        return field_number, wire_type

    def read_bytes(self) -> bytes:
        size = self.read_varint()
        end = self.offset + size
        if end > len(self.payload):
            raise AdbProtocolError("truncated protobuf length-delimited field")
        value = self.payload[self.offset:end]
        self.offset = end
        return value

    def skip(self, wire_type: int) -> None:
        if wire_type == 0:
            self.read_varint()
            return
        if wire_type == 1:
            self._skip_fixed(8)
            return
        if wire_type == 2:
            self.read_bytes()
            return
        if wire_type == 5:
            self._skip_fixed(4)
            return
        raise AdbProtocolError(f"unsupported protobuf wire type {wire_type}")

    def _skip_fixed(self, size: int) -> None:
        end = self.offset + size
        if end > len(self.payload):
            raise AdbProtocolError("truncated protobuf fixed-width field")
        self.offset = end


__all__ = ["ProtoReader"]
