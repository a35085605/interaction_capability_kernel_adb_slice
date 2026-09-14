from __future__ import annotations

import pytest

from _lifecycle_new.resource.cleanup import cleanup_reverse
from adb._deadline import Deadline
from adb.aosp.io._smart_socket_protocol import read_service_response, recv_exact
from adb.errors import AdbServiceError


def test_cleanup_reverse_continues_and_raises_first_error() -> None:
    calls: list[int] = []
    first = RuntimeError("first")

    def close_one(resource: int) -> None:
        calls.append(resource)
        if resource == 2:
            raise first
        if resource == 1:
            raise RuntimeError("later")

    with pytest.raises(RuntimeError) as caught:
        cleanup_reverse((1, 2, 3), close_one)

    assert caught.value is first
    assert calls == [3, 2, 1]


def test_deadline_uses_injected_clock_for_remaining_expiry_and_clamp() -> None:
    now = 10.0

    def clock() -> float:
        return now

    deadline = Deadline.after(5.0, clock)
    assert deadline.remaining() == 5.0
    assert deadline.clamp(10.0) == 5.0
    assert not deadline.expired()

    now = 15.0
    assert deadline.remaining() == 0.0
    assert deadline.clamp(1.0) == 0.0
    assert deadline.expired()


def test_recv_exact_accepts_partial_reads() -> None:
    chunks = iter((b"a", b"bc", b"d"))

    assert recv_exact(lambda _: next(chunks), 4, eof_message="eof") == b"abcd"


def test_service_failure_decoding_is_shared() -> None:
    chunks = iter((b"FAIL", b"0004", b"nope"))

    with pytest.raises(AdbServiceError) as caught:
        read_service_response("host:test", lambda _: next(chunks))

    assert caught.value.service == "host:test"
    assert caught.value.detail == "nope"
