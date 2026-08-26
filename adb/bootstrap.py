from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from adb.epoch import EpochIssuer
from adb.runtime import AdbRuntime
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer, ServerEpoch, ServerEpochSequence
from adb.server.lifecycle.control.controller import AdbServerController
from adb.server.lifecycle.control.backend import AdbServerBackend
from adb.server.lifecycle.control.subprocess import SubprocessAdbServerBackend
from adb.server.lifecycle.supervision.policy import AdbServerSupervisionPolicy
from adb.server.lifecycle.supervision.supervisor import AdbServerSupervisor
from adb.server.provisioning import AdbServerProvisioningState
from adb.server.state import AdbServerState
from adb.tracking.snapshot.identity import (
    AdbDevicesSnapshotEpoch,
    AdbDevicesSnapshotEpochSequence,
)
from adb.tracking.snapshot.state import AdbDevicesSnapshotState
from adb.tracking.supervision.policy import (
    AdbDevicesTrackingSupervisionPolicy,
)
from adb.tracking.supervision.supervisor import (
    AdbDevicesTrackingSupervisor,
)
from adb.transport.lifecycle.ensure import AdbTcpTransportEnsurer
from adb.transport.lifecycle.supervision.policy import (
    AdbConfiguredTransportSupervisionPolicy,
)
from adb.transport.lifecycle.supervision.supervisor import (
    AdbConfiguredTransportSupervisor,
)
from eventing import EventBus
from scheduling import TemporalScheduler


@runtime_checkable
class _AdbServerLifecycleController(Protocol):
    """Bootstrap-private structural type for one complete server lifecycle controller."""

    def provision(self) -> AdbServer:
        ...

    def retire(self, server: AdbServer) -> None:
        ...


_AdbServerControllerFactory = Callable[
    [EpochIssuer[ServerEpoch], AdbServerProvisioningState],
    _AdbServerLifecycleController,
]
_AdbServerBackendFactory = Callable[[], AdbServerBackend]


def _default_server_backend_factory() -> AdbServerBackend:
    return SubprocessAdbServerBackend()


@dataclass(frozen=True, slots=True)
class _BootstrapCore:
    controller: _AdbServerLifecycleController
    provisioning_state: AdbServerProvisioningState
    server_state: AdbServerState
    snapshot_state: AdbDevicesSnapshotState
    devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch]
    initial_server: AdbServer


class AdbRuntimeBootstrap:
    """Composition root for one ADB runtime object graph.

    Bootstrap owns implementation/configuration decisions but does not remain a second runtime
    container.  Each build receives a fresh runtime-scoped epoch issuer, controller graph,
    authoritative server state, and tracked-devices state.
    """

    def __init__(
        self,
        *,
        server_controller_factory: _AdbServerControllerFactory | None = None,
        server_backend_factory: _AdbServerBackendFactory | None = None,
        endpoint: AdbServerEndpoint | None = None,
        pin_endpoint: bool = True,
        server_recovery_enabled: bool = True,
        server_supervision_policy: AdbServerSupervisionPolicy | None = None,
        tracking_supervision_policy: AdbDevicesTrackingSupervisionPolicy | None = None,
        transport_supervision_policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> None:
        if server_controller_factory is not None and not callable(server_controller_factory):
            raise TypeError("server_controller_factory must be callable or None")
        if server_backend_factory is not None and not callable(server_backend_factory):
            raise TypeError("server_backend_factory must be callable or None")
        if server_controller_factory is not None and server_backend_factory is not None:
            raise ValueError(
                "server_controller_factory and server_backend_factory are mutually exclusive"
            )
        if server_backend_factory is None:
            server_backend_factory = _default_server_backend_factory
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")
        if not isinstance(pin_endpoint, bool):
            raise TypeError("pin_endpoint must be bool")
        if not isinstance(server_recovery_enabled, bool):
            raise TypeError("server_recovery_enabled must be bool")
        if server_supervision_policy is None:
            server_supervision_policy = AdbServerSupervisionPolicy()
        if not isinstance(server_supervision_policy, AdbServerSupervisionPolicy):
            raise TypeError(
                "server_supervision_policy must be AdbServerSupervisionPolicy or None"
            )
        if tracking_supervision_policy is None:
            tracking_supervision_policy = AdbDevicesTrackingSupervisionPolicy()
        if not isinstance(
            tracking_supervision_policy, AdbDevicesTrackingSupervisionPolicy
        ):
            raise TypeError(
                "tracking_supervision_policy must be "
                "AdbDevicesTrackingSupervisionPolicy or None"
            )
        if transport_supervision_policy is None:
            transport_supervision_policy = AdbConfiguredTransportSupervisionPolicy()
        if not isinstance(
            transport_supervision_policy, AdbConfiguredTransportSupervisionPolicy
        ):
            raise TypeError(
                "transport_supervision_policy must be "
                "AdbConfiguredTransportSupervisionPolicy or None"
            )

        self._server_controller_factory = server_controller_factory
        self._server_backend_factory = server_backend_factory
        self._endpoint = endpoint
        self._pin_endpoint = pin_endpoint
        self._server_recovery_enabled = server_recovery_enabled
        self._server_supervision_policy = server_supervision_policy
        self._tracking_supervision_policy = tracking_supervision_policy
        self._transport_supervision_policy = transport_supervision_policy

    def build_minimal(self) -> AdbRuntime:
        """Build a runtime with lifecycle capabilities but no supervision automation."""

        core = self._build_core()
        try:
            return self._build_runtime(
                core.server_state,
                core.snapshot_state,
                provisioning_state=core.provisioning_state,
                transport_supervision_policy=self._transport_supervision_policy,
            )
        except BaseException:
            core.controller.retire(core.initial_server)
            raise

    def build_managed(
        self,
        *,
        event_bus: EventBus,
        scheduler: TemporalScheduler[object],
        tcp_transport_ensurer: AdbTcpTransportEnsurer | None = None,
        track_devices: bool = True,
        configured_transports: bool = True,
    ) -> AdbRuntime:
        """Build a runtime with server supervision and optional transport automation."""

        if not _is_event_bus(event_bus):
            raise TypeError("event_bus must satisfy EventBus")
        if not isinstance(scheduler, TemporalScheduler):
            raise TypeError("scheduler must satisfy TemporalScheduler")
        if not isinstance(track_devices, bool):
            raise TypeError("track_devices must be bool")
        if not isinstance(configured_transports, bool):
            raise TypeError("configured_transports must be bool")
        if configured_transports and not track_devices:
            raise ValueError("configured transport supervision requires device tracking")
        if tcp_transport_ensurer is not None and not isinstance(
            tcp_transport_ensurer, AdbTcpTransportEnsurer
        ):
            raise TypeError(
                "tcp_transport_ensurer must satisfy AdbTcpTransportEnsurer or be None"
            )

        core = self._build_core()
        try:
            server_supervisor = AdbServerSupervisor(
                core.server_state,
                controller=core.controller,
                event_bus=event_bus,
                scheduler=scheduler,
                policy=self._server_supervision_policy,
                recovery_enabled=self._server_recovery_enabled,
            )
            tracking_supervisor = (
                AdbDevicesTrackingSupervisor(
                    core.initial_server,
                    event_bus,
                    self._tracking_supervision_policy,
                    server_state=core.server_state,
                    devices_snapshot_epoch_issuer=core.devices_snapshot_epoch_issuer,
                    snapshot_state=core.snapshot_state,
                )
                if track_devices
                else None
            )
            transport_supervisor = (
                AdbConfiguredTransportSupervisor(
                    core.initial_server,
                    event_bus,
                    tcp_transport_ensurer,
                    server_state=core.server_state,
                    devices=core.snapshot_state,
                )
                if configured_transports
                else None
            )
            return self._build_runtime(
                core.server_state,
                core.snapshot_state,
                provisioning_state=core.provisioning_state,
                event_bus=event_bus,
                server_supervisor=server_supervisor,
                tracking_supervisor=tracking_supervisor,
                transport_supervisor=transport_supervisor,
                transport_supervision_policy=self._transport_supervision_policy,
            )
        except BaseException:
            core.controller.retire(core.initial_server)
            raise

    def _build_runtime(self, *args: object, **kwargs: object) -> AdbRuntime:
        return AdbRuntime(*args, **kwargs)

    def _build_core(self) -> _BootstrapCore:
        server_epoch_issuer = ServerEpochSequence()
        devices_snapshot_epoch_issuer = AdbDevicesSnapshotEpochSequence()
        provisioning_state = AdbServerProvisioningState(self._endpoint)
        controller_factory = self._server_controller_factory
        if controller_factory is None:
            backend = self._server_backend_factory()
            if not isinstance(backend, AdbServerBackend):
                raise TypeError(
                    "server backend factory must return AdbServerBackend"
                )
            controller: _AdbServerLifecycleController = AdbServerController(
                backend,
                server_epoch_issuer,
                provisioning_state,
            )
        else:
            controller = controller_factory(server_epoch_issuer, provisioning_state)

        if not isinstance(controller, _AdbServerLifecycleController):
            raise TypeError(
                "server controller factory must return a complete server lifecycle controller"
            )

        initial_server = controller.provision()
        if not isinstance(initial_server, AdbServer):
            raise TypeError("server controller provision() must return AdbServer")
        try:
            if self._endpoint is not None and initial_server.endpoint != self._endpoint:
                raise ValueError("endpoint-constrained initial server provisioning changed endpoint")
            provisioning_state.set_required_endpoint(
                initial_server.endpoint if self._pin_endpoint else None
            )
            return _BootstrapCore(
                controller=controller,
                provisioning_state=provisioning_state,
                server_state=AdbServerState(initial_server),
                snapshot_state=AdbDevicesSnapshotState(),
                devices_snapshot_epoch_issuer=devices_snapshot_epoch_issuer,
                initial_server=initial_server,
            )
        except BaseException:
            controller.retire(initial_server)
            raise


def _is_event_bus(value: object) -> bool:
    return (
        callable(getattr(value, "publish", None))
        and callable(getattr(value, "subscribe", None))
        and callable(getattr(value, "unsubscribe", None))
    )

__all__ = ["AdbRuntimeBootstrap"]
