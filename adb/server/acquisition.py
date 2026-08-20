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
    AdbServerEndpointExhaustedError,
    AdbServerEndpointLease,
    AdbServerEndpointReservation,
    AdbServerEndpointReservationProvider,
    InMemoryAdbServerEndpointProvisioner,
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

    Acquisition never adopts an existing listener. Existing listeners are candidate
    conflicts, and only positive ``CREATED_BY_ATTEMPT`` evidence may enter protocol
    verification for promotion.
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
    """Evidence retained for one reserved candidate."""

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


class AdbServerLease:
    """Verified session-created server evidence plus an endpoint reservation.

    A successful lease proves that this acquisition obtained positive native creation
    evidence and then verified a compatible ADB listener at the same endpoint. It does
    not prove that a later listener at that endpoint is still the same native process.

    Releasing the lease releases only the process-local endpoint reservation and never
    issues ``kill-server``. Process-level managed ownership is provided separately by
    :mod:`adb.server.ownership` and intentionally keeps its lease for process lifetime.
    """

    __slots__ = (
        "endpoint",
        "server_status",
        "attempts",
        "_endpoint_lease",
    )

    def __init__(
        self,
        endpoint_lease: AdbServerEndpointLease,
        server_status: AdbServerStatus,
        attempts: tuple[AdbServerCandidateAttempt, ...],
    ) -> None:
        if not isinstance(endpoint_lease, AdbServerEndpointLease):
            raise TypeError("endpoint_lease must be AdbServerEndpointLease")
        if not endpoint_lease.active:
            raise ValueError("endpoint_lease must be active")
        if not isinstance(server_status, AdbServerStatus):
            raise TypeError("server_status must be AdbServerStatus")
        if not isinstance(attempts, tuple) or not attempts:
            raise ValueError("server lease requires acquisition attempts")
        if not all(isinstance(item, AdbServerCandidateAttempt) for item in attempts):
            raise TypeError("attempts must contain AdbServerCandidateAttempt values")
        if attempts[-1].endpoint != endpoint_lease.endpoint:
            raise ValueError("terminal acquisition attempt must match the leased endpoint")
        if attempts[-1].outcome is not AdbServerCandidateOutcome.CREATED_BY_ACQUISITION:
            raise ValueError(
                "server lease requires terminal CREATED_BY_ACQUISITION evidence"
            )
        self.endpoint = endpoint_lease.endpoint
        self.server_status = server_status
        self.attempts = attempts
        self._endpoint_lease = endpoint_lease

    @property
    def active(self) -> bool:
        return self._endpoint_lease.active

    def release(self) -> None:
        """Release only the process-local reservation, not the native server."""

        self._endpoint_lease.release()

    close = release

    def __enter__(self) -> "AdbServerLease":
        if not self.active:
            raise RuntimeError("ADB server lease is no longer active")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


_MonotonicClock = Callable[[], float]
_Sleeper = Callable[[float], None]


class AdbServerAcquirer:
    """Reserve, observe, create, verify, and promote session-created ADB servers.

    This is the low-level acquisition primitive. It never adopts an already-running
    listener. Canonical managed callers should acquire through
    :func:`adb.server.acquire_process_adb_server`, which serializes acquisition into the
    process-level server slot.
    """

    def __init__(
        self,
        reservation_provider: AdbServerEndpointReservationProvider | None = None,
        observer: AdbServerEndpointObserver | None = None,
        creator: AdbServerCreator | None = None,
        *,
        _monotonic: _MonotonicClock = monotonic,
        _sleep: _Sleeper = sleep,
    ) -> None:
        if reservation_provider is None:
            reservation_provider = InMemoryAdbServerEndpointProvisioner()
        if observer is None:
            observer = SmartSocketAdbServerEndpointObserver()
        if creator is None:
            from adb.server.lifecycle.adapters import SubprocessAdbServerCreator

            creator = SubprocessAdbServerCreator()
        if not callable(getattr(reservation_provider, "reserve", None)):
            raise TypeError("reservation_provider must provide reserve()")
        if not callable(getattr(observer, "observe", None)):
            raise TypeError("observer must provide observe()")
        if not callable(getattr(creator, "create", None)):
            raise TypeError("creator must provide create()")
        if not callable(_monotonic):
            raise TypeError("_monotonic must be callable")
        if not callable(_sleep):
            raise TypeError("_sleep must be callable")
        self._reservations = reservation_provider
        self._observer = observer
        self._creator = creator
        self._monotonic = _monotonic
        self._sleep = _sleep

    def acquire(
        self,
        policy: AdbServerAcquisitionPolicy,
        *,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerLease:
        if not isinstance(policy, AdbServerAcquisitionPolicy):
            raise TypeError("policy must be AdbServerAcquisitionPolicy")
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        attempts: list[AdbServerCandidateAttempt] = []
        attempted_endpoints: set[AdbServerEndpoint] = set()
        candidate_limit = 1 if endpoint is not None else policy.max_candidates

        for _ in range(candidate_limit):
            try:
                reservation = self._reservations.reserve(
                    endpoint=endpoint,
                    excluded_endpoints=frozenset(attempted_endpoints),
                )
            except AdbServerEndpointExhaustedError:
                break
            if not isinstance(reservation, AdbServerEndpointReservation):
                raise TypeError("reserve() must return AdbServerEndpointReservation")
            candidate = reservation.endpoint
            attempted_endpoints.add(candidate)

            try:
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

                verification = (
                    self._verify(candidate, policy)
                    if creation.evidence is AdbServerCreationEvidence.CREATED_BY_ATTEMPT
                    else ()
                )
                verified = self._last_verified(verification)
                if (
                    creation.evidence is AdbServerCreationEvidence.CREATED_BY_ATTEMPT
                    and verified is not None
                ):
                    record = AdbServerCandidateAttempt(
                        candidate,
                        precheck,
                        AdbServerCandidateOutcome.CREATED_BY_ACQUISITION,
                        creation,
                        verification,
                    )
                    return self._promote(
                        reservation,
                        verified,
                        tuple([*attempts, record]),
                    )

                outcome = (
                    AdbServerCandidateOutcome.VERIFICATION_FAILED
                    if creation.evidence is AdbServerCreationEvidence.CREATED_BY_ATTEMPT
                    else AdbServerCandidateOutcome.CREATION_NOT_CONFIRMED
                )
                attempts.append(
                    AdbServerCandidateAttempt(
                        candidate,
                        precheck,
                        outcome,
                        creation,
                        verification,
                    )
                )
            finally:
                reservation.release()

        raise AdbServerAcquisitionError(tuple(attempts))

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

    @staticmethod
    def _promote(
        reservation: AdbServerEndpointReservation,
        verified: EndpointObservation,
        attempts: tuple[AdbServerCandidateAttempt, ...],
    ) -> AdbServerLease:
        if verified.server_status is None:
            raise ValueError("lease promotion requires a verified server status")
        endpoint_lease = reservation.promote()
        try:
            return AdbServerLease(
                endpoint_lease,
                verified.server_status,
                attempts,
            )
        except BaseException:
            endpoint_lease.release()
            raise


__all__ = [
    "AdbServerAcquirer",
    "AdbServerAcquisitionError",
    "AdbServerAcquisitionPolicy",
    "AdbServerCandidateAttempt",
    "AdbServerCandidateOutcome",
    "AdbServerLease",
]
