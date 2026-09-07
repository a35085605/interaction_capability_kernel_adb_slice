from __future__ import annotations

from typing import TYPE_CHECKING

from adb.transport_list.model import AdbTransportList
from adb.transport_list.observation import AdbTransportListObservation
from adb.transport_list.state import (
    AdbTransportListCoordinatedObservationResult,
    AdbTransportListInvalidated,
    AdbTransportListObservationResult,
    AdbTransportListObservationStateConflict,
    AdbTransportListObserved,
    AdbTransportListState,
    AdbTransportListStateAuthority,
)
from eventing import EventPublisher

if TYPE_CHECKING:
    from adb.transport_list.watch.backend import (
        AdbTransportListWatchBackend,
        AdbTransportListWatchBackendReleaseResult,
    )
    from adb.transport_list.watch.generation import AdbTransportListWatchGeneration


def _require_generation(value: object) -> AdbTransportListWatchGeneration:
    from adb.transport_list.watch.generation import AdbTransportListWatchGeneration

    if not isinstance(value, AdbTransportListWatchGeneration):
        raise TypeError("generation must be AdbTransportListWatchGeneration")
    return value


class AdbTransportListCoordinator:
    """Coordinate generation-fenced watch observations with projection state."""

    def __init__(
        self,
        backend: AdbTransportListWatchBackend,
        authority: AdbTransportListStateAuthority,
        *,
        publisher: EventPublisher | None = None,
    ) -> None:
        from adb.transport_list.watch.backend import AdbTransportListWatchBackend

        if not isinstance(backend, AdbTransportListWatchBackend):
            raise TypeError("backend must satisfy AdbTransportListWatchBackend")
        if not isinstance(authority, AdbTransportListStateAuthority):
            raise TypeError("authority must satisfy AdbTransportListStateAuthority")
        if publisher is not None and not isinstance(publisher, EventPublisher):
            raise TypeError("publisher must satisfy EventPublisher or be None")
        self._backend = backend
        self._authority = authority
        self._publisher = publisher

    @property
    def backend(self) -> AdbTransportListWatchBackend:
        return self._backend

    @property
    def authority(self) -> AdbTransportListStateAuthority:
        return self._authority

    def can_observe_update(self, generation: AdbTransportListWatchGeneration) -> bool:
        """Whether ``generation`` may enter another blocking update read right now."""

        _require_generation(generation)
        state = self._backend.read()
        return state.active and state.generation == generation

    def observe_initial(
        self,
        generation: AdbTransportListWatchGeneration,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult:
        return self._observe(generation, transport_list, initial=True)

    def observe_update(
        self,
        generation: AdbTransportListWatchGeneration,
        transport_list: AdbTransportList,
    ) -> AdbTransportListObservationResult:
        return self._observe(generation, transport_list, initial=False)

    def release(
        self,
        expected: AdbTransportListWatchGeneration,
    ) -> AdbTransportListWatchBackendReleaseResult:
        """Release matching watch authority and invalidate only its retained projection."""

        from adb.transport_list.watch.backend import AdbTransportListWatchBackendReleased

        _require_generation(expected)
        result = self._backend.release(expected)
        if isinstance(result, AdbTransportListWatchBackendReleased):
            invalidation = self._authority.invalidate_generation(expected)
            if (
                isinstance(invalidation, AdbTransportListInvalidated)
                and self._publisher is not None
            ):
                self._publisher.publish(invalidation)
        return result

    def _observe(
        self,
        generation: AdbTransportListWatchGeneration,
        transport_list: AdbTransportList,
        *,
        initial: bool,
    ) -> AdbTransportListObservationResult:
        _require_generation(generation)
        if not isinstance(transport_list, AdbTransportList):
            raise TypeError("transport_list must be AdbTransportList")

        committed_state: AdbTransportListState | None = None

        def commit() -> None:
            nonlocal committed_state
            committed_state = (
                self._authority.observe_initial(generation, transport_list)
                if initial
                else self._authority.observe_update(generation, transport_list)
            )

        current = self._backend.run_if_current(generation, commit)
        if not current or committed_state is None:
            return AdbTransportListObservationStateConflict(
                generation,
                transport_list,
                self._authority.snapshot(),
            )

        observation = AdbTransportListObservation(
            generation=generation,
            identity=committed_state.identity,
            transport_list=transport_list,
        )
        result = AdbTransportListObserved(observation)
        if self._publisher is not None:
            self._publisher.publish(result)
        return result


__all__ = [
    "AdbTransportListCoordinator",
    "AdbTransportListCoordinatedObservationResult",
]
