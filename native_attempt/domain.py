from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


def _normalize_non_empty_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty")
    return normalized


def _normalize_optional_text(
    value: object,
    *,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _normalize_non_empty_text(value, field_name=field_name)


def _require_timezone_aware(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be datetime")
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class NativeAttemptStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class NativeCompletionScope(str, Enum):
    """Strongest native boundary known to have completed for an attempt."""

    SUBMISSION = "submission"
    SYNCHRONOUS_RETURN = "synchronous_return"
    PROCESS_EXIT = "process_exit"


@dataclass(frozen=True, slots=True)
class NativeAttemptResult:
    """Terminal evidence from one native attempt.

    Application-level effects are evaluated separately.
    """

    status: NativeAttemptStatus
    completion_scope: NativeCompletionScope | None
    backend_id: str
    started_at: datetime
    finished_at: datetime
    native_code: str | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, NativeAttemptStatus):
            raise TypeError("native attempt status must be NativeAttemptStatus")
        if self.completion_scope is not None and not isinstance(
            self.completion_scope,
            NativeCompletionScope,
        ):
            raise TypeError(
                "native completion_scope must be NativeCompletionScope or None"
            )
        if (
            self.status is NativeAttemptStatus.SUCCEEDED
            and self.completion_scope is None
        ):
            raise ValueError(
                "a successful native attempt requires completion_scope"
            )
        started_at = _require_timezone_aware(
            self.started_at,
            field_name="native attempt started_at",
        )
        finished_at = _require_timezone_aware(
            self.finished_at,
            field_name="native attempt finished_at",
        )
        if finished_at < started_at:
            raise ValueError("native attempt cannot finish before it starts")
        object.__setattr__(
            self,
            "backend_id",
            _normalize_non_empty_text(
                self.backend_id,
                field_name="native attempt backend id",
            ),
        )
        object.__setattr__(
            self,
            "native_code",
            _normalize_optional_text(
                self.native_code,
                field_name="native attempt code",
            ),
        )
        object.__setattr__(
            self,
            "diagnostic",
            _normalize_optional_text(
                self.diagnostic,
                field_name="native attempt diagnostic",
            ),
        )


__all__ = [
    "NativeAttemptResult",
    "NativeAttemptStatus",
    "NativeCompletionScope",
]
