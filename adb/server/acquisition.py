from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from math import ceil, isfinite
from numbers import Integral, Real
from time import monotonic, sleep

from adb.server.endpoint.model import AdbServerEndpoint
from adb.server.endpoint.observation import (
    AdbServerEndpointObserver,
    EndpointObservation,
    EndpointObservationStatus,
    SmartSocketAdbServerEndpointObserver,
)
from adb.server.endpoint.provisioning import (
    AdbServerEndpointAllocator,
    AdbServerEndpointExhaustedError,
    SequentialAdbServerEndpointAllocator,
)
from adb.server.lifecycle.creation import (
    AdbServerCreationAttempt,
    AdbServerCreationEvidence,
    AdbServerCreator,
)
from adb.server.status.model import AdbServerStatus


def _positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{field_name} must be an integer")
    normalized = int(value)
    if normalized <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return normalized


def _nonnegative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{field_name} must be an integer")
    normalized = int(value)
    if normalized < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return normalized


def _positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and greater than zero")
    return normalized


@dataclass(frozen=True, slots=True)
class AdbServerAcquisitionPolicy:
    """Bounded candidate and verification policy for one session-created acquisition.

    Existing listeners are never adopted. Candidate search may advance only while no native
    creation may have occurred. Once a creation attempt reports positive or indeterminate
    creation evidence, that endpoint becomes the terminal candidate for the acquisition.
    """

    max_candidates: int = 32
    indeterminate_retries: int = 0
    verification_timeout_seconds: float = 5.0
    probe_interval_seconds: float = 0.05

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_candidates",
            _positive_int(self.max_candidates, field_name="max_candidates"),
        )
        object.__setattr__(
            self,
            "indeterminate_retries",
            _nonnegative_int(
                self.indeterminate_retries,
                field_name="indeterminate_retries",
            ),
        )
        object.__setattr__(
            self,
            "verification_timeout_seconds",
            _positive_seconds(
                self.verification_timeout_seconds,
                field_name="verification_timeout_seconds",
            ),
        )
        object.__setattr__(
            self,
            "probe_interval_seconds",
            _positive_seconds(
                self.probe_interval_seconds,
                field_name="probe_interval_seconds",
            ),
        )


class AdbServerCandidateOutcome(str, Enum):
    CREATED_BY_ACQUISITION = "created_by_acquisition"
    OCCUPIED = "occupied"
    INDETERMINATE = "indeterminate"
    CREATION_NOT_CONFIRMED = "creation_not_confirmed"
    VERIFICATION_FAILED = "verification_failed"


@dataclass(frozen=True, slots=True)
class AdbServerCandidateAttempt:
    """Evidence retained for one candidate endpoint."""

    endpoint: AdbServerEndpoint
    precheck_observations: tuple[EndpointObservation, ...]
    outcome: AdbServerCandidateOutcome
    creation_attempt: AdbServerCreationAttempt | None = None
    verification_observations: tuple[EndpointObservation, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(self.precheck_observations, tuple) or not self.precheck_observations:
            raise ValueError("candidate attempt requires precheck observations")
        if not isinstance(self.verification_observations, tuple):
            raise TypeError("verification_observations must be a tuple")
        observations = self.precheck_observations + self.verification_observations
        if not all(isinstance(item, EndpointObservation) for item in observations):
            raise TypeError("candidate observations must be EndpointObservation values")
        if any(item.endpoint != self.endpoint for item in observations):
            raise ValueError("candidate observations must match the candidate endpoint")
        if not isinstance(self.outcome, AdbServerCandidateOutcome):
            raise TypeError("outcome must be AdbServerCandidateOutcome")
        if self.creation_attempt is not None:
            if not isinstance(self.creation_attempt, AdbServerCreationAttempt):
                raise TypeError("creation_attempt must be AdbServerCreationAttempt or None")
            if self.creation_attempt.endpoint != self.endpoint:
                raise ValueError("creation attempt must match the candidate endpoint")

    @property
    def native_creation_may_have_occurred(self) -> bool:
        """Whether this candidate may now contain a native server created by this attempt."""

        creation = self.creation_attempt
        return creation is not None and creation.evidence in {
            AdbServerCreationEvidence.CREATED_BY_ATTEMPT,
            AdbServerCreationEvidence.INDETERMINATE,
        }


class AdbServerAcquisitionError(RuntimeError):
    """No candidate satisfied session-created acquisition requirements."""

    def __init__(self, attempts: tuple[AdbServerCandidateAttempt, ...]) -> None:
        if not isinstance(attempts, tuple) or not all(
            isinstance(attempt, AdbServerCandidateAttempt) for attempt in attempts
        ):
            raise TypeError("attempts must be a tuple of AdbServerCandidateAttempt")
        self.attempts = attempts
        detail = (
            ", ".join(
                f"{attempt.endpoint.host}:{attempt.endpoint.port}={attempt.outcome.value}"
                for attempt in attempts
            )
            or "no endpoint candidates were available"
        )
        super().__init__(f"ADB server acquisition failed: {detail}")

    @property
    def pinned_endpoint(self) -> AdbServerEndpoint | None:
        """Endpoint that must remain pinned after a possibly mutating failed creation."""

        if not self.attempts:
            return None
        terminal = self.attempts[-1]
        return terminal.endpoint if terminal.native_creation_may_have_occurred else None


@dataclass(frozen=True, slots=True)
class AdbServerAcquisition:
    """Immutable evidence for one verified server created by this acquisition.

    This receipt is not a native lifetime lease. Runtime ownership is established only when
    the process singleton slot promotes the receipt into an :class:`AdbOwnedServer`.
    """

    endpoint: AdbServerEndpoint
    server_status: AdbServerStatus
    attempts: tuple[AdbServerCandidateAttempt, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        if not isinstance(self.server_status, AdbServerStatus):
            raise TypeError("server_status must be AdbServerStatus")
        if not isinstance(self.attempts, tuple) or not self.attempts:
            raise ValueError("server acquisition requires acquisition attempts")
        if not all(isinstance(item, AdbServerCandidateAttempt) for item in self.attempts):
            raise TypeError("attempts must contain AdbServerCandidateAttempt values")
        terminal = self.attempts[-1]
        if terminal.endpoint != self.endpoint:
            raise ValueError("terminal acquisition attempt must match the acquired endpoint")
        if terminal.outcome is not AdbServerCandidateOutcome.CREATED_BY_ACQUISITION:
            raise ValueError(
                "server acquisition requires terminal CREATED_BY_ACQUISITION evidence"
            )


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]


class AdbServerAcquirer:
    """Allocate, observe, create, and verify session-created ADB servers.

    This low-level primitive never adopts an already-running listener. Candidate allocation
    is acquisition-local bookkeeping only; operating-system bind success is decided by the
    native creator. After any possibly mutating creation result, acquisition stops on that
    endpoint instead of searching another candidate.
    """

    def __init__(
        self,
        endpoint_allocator: AdbServerEndpointAllocator | None = None,
        observer: AdbServerEndpointObserver | None = None,
        creator: AdbServerCreator | None = None,
        *,
        _monotonic: _MonotonicClock = monotonic,
        _sleep: _Sleeper = sleep,
    ) -> None:
        if endpoint_allocator is None:
            endpoint_allocator = SequentialAdbServerEndpointAllocator()
        if observer is None:
            observer = SmartSocketAdbServerEndpointObserver()
        if creator is None:
            from adb.server.lifecycle.adapters import SubprocessAdbServerCreator

            creator = SubprocessAdbServerCreator()
        if not callable(getattr(endpoint_allocator, "allocate", None)):
            raise TypeError("endpoint_allocator must provide allocate()")
        if not callable(getattr(observer, "observe", None)):
            raise TypeError("observer must provide observe()")
        if not callable(getattr(creator, "create", None)):
            raise TypeError("creator must provide create()")
        if not callable(_monotonic):
            raise TypeError("_monotonic must be callable")
        if not callable(_sleep):
            raise TypeError("_sleep must be callable")
        self._endpoint_allocator = endpoint_allocator
        self._observer = observer
        self._creator = creator
        self._monotonic = _monotonic
        self._sleep = _sleep

    def acquire(
        self,
        policy: AdbServerAcquisitionPolicy,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerAcquisition:
        if not isinstance(policy, AdbServerAcquisitionPolicy):
            raise TypeError("policy must be AdbServerAcquisitionPolicy")
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        attempts: list[AdbServerCandidateAttempt] = []
        attempted_endpoints: set[AdbServerEndpoint] = set()
        candidate_limit = 1 if endpoint is not None else policy.max_candidates

        for _ in range(candidate_limit):
            try:
                candidate = self._next_candidate(endpoint, attempted_endpoints)
            except AdbServerEndpointExhaustedError:
                break
            attempted_endpoints.add(candidate)

            precheck = self._precheck(candidate, policy)
            initial = precheck[-1]

            if initial.status is EndpointObservationStatus.ADB_SERVER_VERIFIED:
                attempts.append(
                    AdbServerCandidateAttempt(
                        candidate,
                        precheck,
                        AdbServerCandidateOutcome.OCCUPIED,
                    )
                )
                continue

            if initial.status is EndpointObservationStatus.INDETERMINATE:
                attempts.append(
                    AdbServerCandidateAttempt(
                        candidate,
                        precheck,
                        AdbServerCandidateOutcome.INDETERMINATE,
                    )
                )
                continue

            if initial.status is not EndpointObservationStatus.NO_LISTENER_OBSERVED:
                attempts.append(
                    AdbServerCandidateAttempt(
                        candidate,
                        precheck,
                        AdbServerCandidateOutcome.OCCUPIED,
                    )
                )
                continue

            creation = self._creator.create(candidate)
            if not isinstance(creation, AdbServerCreationAttempt):
                raise TypeError("creator.create() must return AdbServerCreationAttempt")
            if creation.endpoint != candidate:
                raise ValueError("creation attempt endpoint does not match candidate")

            if creation.evidence is AdbServerCreationEvidence.CREATED_BY_ATTEMPT:
                verification = self._verify(candidate, policy)
                verified = self._last_verified(verification)
                if verified is not None:
                    record = AdbServerCandidateAttempt(
                        candidate,
                        precheck,
                        AdbServerCandidateOutcome.CREATED_BY_ACQUISITION,
                        creation,
                        verification,
                    )
                    if verified.server_status is None:
                        raise ValueError("verified acquisition requires server status")
                    return AdbServerAcquisition(
                        candidate,
                        verified.server_status,
                        tuple([*attempts, record]),
                    )

                attempts.append(
                    AdbServerCandidateAttempt(
                        candidate,
                        precheck,
                        AdbServerCandidateOutcome.VERIFICATION_FAILED,
                        creation,
                        verification,
                    )
                )
                raise AdbServerAcquisitionError(tuple(attempts))

            attempts.append(
                AdbServerCandidateAttempt(
                    candidate,
                    precheck,
                    AdbServerCandidateOutcome.CREATION_NOT_CONFIRMED,
                    creation,
                )
            )
            if creation.evidence is AdbServerCreationEvidence.INDETERMINATE:
                raise AdbServerAcquisitionError(tuple(attempts))

        raise AdbServerAcquisitionError(tuple(attempts))

    def _next_candidate(
        self,
        endpoint: AdbServerEndpoint | None,
        attempted_endpoints: set[AdbServerEndpoint],
    ) -> AdbServerEndpoint:
        if endpoint is not None:
            return endpoint
        candidate = self._endpoint_allocator.allocate(frozenset(attempted_endpoints))
        if not isinstance(candidate, AdbServerEndpoint):
            raise TypeError("endpoint_allocator.allocate() must return AdbServerEndpoint")
        if candidate in attempted_endpoints:
            raise ValueError("endpoint allocator returned an excluded endpoint")
        return candidate

    def _precheck(
        self,
        endpoint: AdbServerEndpoint,
        policy: AdbServerAcquisitionPolicy,
    ) -> tuple[EndpointObservation, ...]:
        observations: list[EndpointObservation] = []
        for retry in range(policy.indeterminate_retries + 1):
            observation = self._observe(endpoint)
            observations.append(observation)
            if observation.status is not EndpointObservationStatus.INDETERMINATE:
                break
            if retry < policy.indeterminate_retries:
                self._sleep(policy.probe_interval_seconds)
        return tuple(observations)

    def _verify(
        self,
        endpoint: AdbServerEndpoint,
        policy: AdbServerAcquisitionPolicy,
    ) -> tuple[EndpointObservation, ...]:
        deadline = self._monotonic() + policy.verification_timeout_seconds
        maximum_probes = (
            ceil(policy.verification_timeout_seconds / policy.probe_interval_seconds)
            + 1
        )
        observations: list[EndpointObservation] = []
        for probe_index in range(maximum_probes):
            observation = self._observe(endpoint)
            observations.append(observation)
            if observation.status is EndpointObservationStatus.ADB_SERVER_VERIFIED:
                break
            remaining = deadline - self._monotonic()
            if remaining <= 0.0 or probe_index + 1 >= maximum_probes:
                break
            self._sleep(min(policy.probe_interval_seconds, remaining))
        return tuple(observations)

    def _observe(self, endpoint: AdbServerEndpoint) -> EndpointObservation:
        observation = self._observer.observe(endpoint)
        if not isinstance(observation, EndpointObservation):
            raise TypeError("observer.observe() must return EndpointObservation")
        if observation.endpoint != endpoint:
            raise ValueError("endpoint observation does not match candidate")
        return observation

    @staticmethod
    def _last_verified(
        observations: tuple[EndpointObservation, ...],
    ) -> EndpointObservation | None:
        if not observations:
            return None
        observation = observations[-1]
        return (
            observation
            if observation.status is EndpointObservationStatus.ADB_SERVER_VERIFIED
            else None
        )


__all__ = [
    "AdbServerAcquirer",
    "AdbServerAcquisition",
    "AdbServerAcquisitionError",
    "AdbServerAcquisitionPolicy",
    "AdbServerCandidateAttempt",
    "AdbServerCandidateOutcome",
]
