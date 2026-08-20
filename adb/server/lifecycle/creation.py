from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from native_attempt import NativeAttemptResult, NativeAttemptStatus, NativeCompletionScope


class AdbServerCreationEvidence(str, Enum):
    """Strongest creation fact established by one native launch attempt."""

    CREATED_BY_ATTEMPT = "created_by_attempt"
    NOT_CREATED = "not_created"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class AdbServerCreationAttempt:
    """Native launch evidence kept separate from protocol verification."""

    endpoint: AdbServerEndpoint
    evidence: AdbServerCreationEvidence
    native_attempt: NativeAttemptResult

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(self.evidence, AdbServerCreationEvidence):
            raise TypeError("evidence must be AdbServerCreationEvidence")
        if not isinstance(self.native_attempt, NativeAttemptResult):
            raise TypeError("native_attempt must be NativeAttemptResult")
        if self.evidence is AdbServerCreationEvidence.CREATED_BY_ATTEMPT:
            if self.native_attempt.status is not NativeAttemptStatus.SUCCEEDED:
                raise ValueError("positive creation evidence requires a successful native attempt")
            if self.native_attempt.completion_scope is not NativeCompletionScope.PROCESS_EXIT:
                raise ValueError(
                    "positive creation evidence requires the launcher process to exit"
                )
        elif self.evidence is AdbServerCreationEvidence.NOT_CREATED:
            if self.native_attempt.status is not NativeAttemptStatus.FAILED:
                raise ValueError("negative creation evidence requires a failed native attempt")
            if self.native_attempt.completion_scope is not NativeCompletionScope.PROCESS_EXIT:
                raise ValueError(
                    "negative creation evidence requires the launcher process to exit"
                )


@runtime_checkable
class AdbServerCreator(Protocol):
    """Launch a new ADB server without adopting an existing listener."""

    def create(self, endpoint: AdbServerEndpoint) -> AdbServerCreationAttempt: ...


__all__ = [
    "AdbServerCreationAttempt",
    "AdbServerCreationEvidence",
    "AdbServerCreator",
]
