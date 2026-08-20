from __future__ import annotations

from datetime import timedelta
import re

from adb.server import AdbServerEndpoint
from adb.transport import AdbTransportSelector
from android.adb.adapters._attempt import ClientFactory, default_client_factory, shell_v2_attempt
from android.display import AndroidDisplayId
from android.spatial import AndroidDisplayPoint
from execution.input import KeyChord, KeyPress, TextEntry
from execution.touch import TouchDragAndDrop, TouchLongPress, TouchSwipe, TouchTap
from native_attempt import NativeAttemptResult, NativeAttemptStatus


_SAFE_KEY_RE = re.compile(r"^(?:KEYCODE_)?[A-Z0-9_]+$")
_PORTABLE_TEXT_RE = re.compile(r"^[A-Za-z0-9 .,_@+\-]+$")


def _duration_ms(value: timedelta) -> int:
    return max(1, int(round(value.total_seconds() * 1000)))


def _keycode(key_value: str) -> str:
    raw = key_value.upper()
    if _SAFE_KEY_RE.fullmatch(raw) is None:
        raise ValueError("Android key name contains unsupported characters")
    return raw if raw.startswith("KEYCODE_") else f"KEYCODE_{raw}"


class _AndroidAdbCommandAdapter:
    backend_id = "android-adb-input"

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        display_id: AndroidDisplayId,
        *,
        _client_factory: ClientFactory = default_client_factory,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(display_id, AndroidDisplayId):
            raise TypeError("display_id must be AndroidDisplayId")
        self.endpoint = endpoint
        self.selector = selector
        self.display_id = display_id
        self._client_factory = _client_factory

    def _attempt(self, command: str) -> NativeAttemptResult:
        return shell_v2_attempt(
            self.endpoint,
            self.selector,
            command,
            backend_id=self.backend_id,
            client_factory=self._client_factory,
        )


class AdbTouchController(_AndroidAdbCommandAdapter):
    """Single-pointer Android touch commands in one logical display surface."""

    def tap(self, operation: TouchTap[AndroidDisplayPoint]) -> NativeAttemptResult:
        if not isinstance(operation, TouchTap):
            raise TypeError("operation must be TouchTap")
        point = _require_display_point(operation.point)
        return self._attempt(
            f"input -d {self.display_id.value} tap {point.x} {point.y}"
        )

    def long_press(
        self,
        operation: TouchLongPress[AndroidDisplayPoint],
    ) -> NativeAttemptResult:
        if not isinstance(operation, TouchLongPress):
            raise TypeError("operation must be TouchLongPress")
        point = _require_display_point(operation.point)
        duration = _duration_ms(operation.duration)
        return self._attempt(
            "input -d "
            f"{self.display_id.value} swipe "
            f"{point.x} {point.y} {point.x} {point.y} {duration}"
        )

    def swipe(self, operation: TouchSwipe[AndroidDisplayPoint]) -> NativeAttemptResult:
        if not isinstance(operation, TouchSwipe):
            raise TypeError("operation must be TouchSwipe")
        start = _require_display_point(operation.start)
        end = _require_display_point(operation.end)
        duration = _duration_ms(operation.duration)
        return self._attempt(
            "input -d "
            f"{self.display_id.value} swipe "
            f"{start.x} {start.y} {end.x} {end.y} {duration}"
        )

    def drag_and_drop(
        self,
        operation: TouchDragAndDrop[AndroidDisplayPoint],
    ) -> NativeAttemptResult:
        if not isinstance(operation, TouchDragAndDrop):
            raise TypeError("operation must be TouchDragAndDrop")
        start = _require_display_point(operation.start)
        end = _require_display_point(operation.end)
        duration = _duration_ms(operation.duration)
        return self._attempt(
            "input -d "
            f"{self.display_id.value} draganddrop "
            f"{start.x} {start.y} {end.x} {end.y} {duration}"
        )


class AdbKeyPresser(_AndroidAdbCommandAdapter):
    """Basic Android key-event adapter for one logical display."""

    def press(self, operation: KeyPress) -> NativeAttemptResult:
        if not isinstance(operation, KeyPress):
            raise TypeError("operation must be KeyPress")
        if operation.repeat != 1 or operation.interval.total_seconds() != 0:
            return self._unsupported_repeat_result()
        return self._attempt(
            f"input -d {self.display_id.value} keyevent {_keycode(operation.key.value)}"
        )

    def _unsupported_repeat_result(self) -> NativeAttemptResult:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return NativeAttemptResult(
            status=NativeAttemptStatus.FAILED,
            completion_scope=None,
            backend_id=self.backend_id,
            started_at=now,
            finished_at=now,
            native_code="unsupported_semantics",
            diagnostic="Android ADB key adapter currently requires repeat=1 and interval=0",
        )


class AdbKeyChordController(_AndroidAdbCommandAdapter):
    """One AOSP ``input keycombination`` native attempt."""

    def chord(self, operation: KeyChord) -> NativeAttemptResult:
        if not isinstance(operation, KeyChord):
            raise TypeError("operation must be KeyChord")
        keycodes = " ".join(_keycode(key.value) for key in operation.keys)
        return self._attempt(
            f"input -d {self.display_id.value} keycombination {keycodes}"
        )


class AdbTextController(_AndroidAdbCommandAdapter):
    """Limited portable ASCII text via AOSP virtual-keyboard ``input text`` semantics."""

    def type_text(self, operation: TextEntry) -> NativeAttemptResult:
        if not isinstance(operation, TextEntry):
            raise TypeError("operation must be TextEntry")
        if _PORTABLE_TEXT_RE.fullmatch(operation.text) is None:
            now_result = self._unsupported_text_result()
            return now_result
        encoded = operation.text.replace(" ", "%s")
        return self._attempt(f"input -d {self.display_id.value} text {encoded}")

    def _unsupported_text_result(self) -> NativeAttemptResult:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return NativeAttemptResult(
            status=NativeAttemptStatus.FAILED,
            completion_scope=None,
            backend_id=self.backend_id,
            started_at=now,
            finished_at=now,
            native_code="unsupported_text_semantics",
            diagnostic=(
                "Android ADB text adapter supports only portable ASCII letters, digits, "
                "space, and .,_@+-. Use an IME/clipboard/instrumentation adapter for "
                "general Unicode"
            ),
        )


class AdbBackNavigator(_AndroidAdbCommandAdapter):
    def back(self) -> NativeAttemptResult:
        return self._attempt(
            f"input -d {self.display_id.value} keyevent KEYCODE_BACK"
        )


def _require_display_point(value: object) -> AndroidDisplayPoint:
    if not isinstance(value, AndroidDisplayPoint):
        raise TypeError("Android touch point must be AndroidDisplayPoint")
    return value


__all__ = [
    "AdbBackNavigator",
    "AdbKeyChordController",
    "AdbKeyPresser",
    "AdbTextController",
    "AdbTouchController",
]
