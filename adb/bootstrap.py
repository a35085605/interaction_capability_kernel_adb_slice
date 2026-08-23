from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from adb.runtime import AdbRuntime
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer, AdbServerEpochIssuer, AdbServerEpochSequence
from adb.server.lifecycle.control.port import AdbServerController
from adb.server.lifecycle.control.subprocess import SubprocessAdbServerController
from adb.server.lifecycle.provisioning.policy import (
    AdbServerEndpointPolicy,
    AdbServerFixedEndpoint,
    AdbServerPerGenerationEndpoint,
    AdbServerPinFirstResolvedEndpoint,
    resolve_server_provisioning_endpoint,
)
from adb.server.lifecycle.provisioning.provisioner import AdbServerControllerProvisioner
from adb.server.lifecycle.supervision.policy import AdbServerSupervisionPolicy
from adb.server.lifecycle.supervision.supervisor import AdbServerSupervisor
from adb.server.state import AdbServerState
from adb.transport.inventory.state import AdbDevicesInventoryState
from adb.transport.inventory.tracking.supervision.policy import (
    AdbDevicesTrackingSupervisionPolicy,
)
from adb.transport.inventory.tracking.supervision.supervisor import (
    AdbDevicesTrackingSupervisor,
)
from adb.transport.lifecycle.ensure import AdbTransportEnsurer
from adb.transport.lifecycle.supervision.policy import (
    AdbConfiguredTransportSupervisionPolicy,
)
from adb.transport.lifecycle.supervision.supervisor import (
    AdbConfiguredTransportSupervisor,
)
from eventing import EventBus
from scheduling import TemporalScheduler


_AdbServerControllerFactory = Callable[[AdbServerEpochIssuer], AdbServerController]


def _default_server_controller_factory(
    issuer: AdbServerEpochIssuer,
) -> AdbServerController:
    return SubprocessAdbServerController(server_epoch_issuer=issuer)


@dataclass(frozen=True, slots=True)
class _BootstrapCore:
    controller: AdbServerController
    provisioner: AdbServerControllerProvisioner
    server_state: AdbServerState
    inventory_state: AdbDevicesInventoryState
    initial_server: AdbServer


class AdbRuntimeBootstrap:
    """Composition root for one ADB runtime object graph.

    Bootstrap owns implementation/configuration decisions but does not remain a second runtime
    container.  Each build receives a fresh runtime-scoped epoch issuer, controller graph,
    authoritative server state, and inventory state.
    """

    def __init__(
        self,
        *,
        server_controller_factory: _AdbServerControllerFactory | None = None,
        initial_endpoint: AdbServerEndpoint | None = None,
        endpoint_policy: AdbServerEndpointPolicy | None = None,
        server_supervision_policy: AdbServerSupervisionPolicy | None = None,
        tracking_supervision_policy: AdbDevicesTrackingSupervisionPolicy | None = None,
        transport_supervision_policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> None:
        if server_controller_factory is None:
            server_controller_factory = _default_server_controller_factory
        if not callable(server_controller_factory):
            raise TypeError("server_controller_factory must be callable")
        if initial_endpoint is not None and not isinstance(initial_endpoint, AdbServerEndpoint):
            raise TypeError("initial_endpoint must be AdbServerEndpoint or None")
        if endpoint_policy is None:
            endpoint_policy = AdbServerPinFirstResolvedEndpoint()
        if not isinstance(
            endpoint_policy,
            (
                AdbServerPerGenerationEndpoint,
                AdbServerPinFirstResolvedEndpoint,
                AdbServerFixedEndpoint,
            ),
        ):
            raise TypeError("endpoint_policy must be an ADB server endpoint policy")
        if (
            isinstance(endpoint_policy, AdbServerFixedEndpoint)
            and initial_endpoint is not None
            and initial_endpoint != endpoint_policy.endpoint
        ):
            raise ValueError("initial_endpoint must match fixed ADB server endpoint policy")
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
        self._initial_endpoint = initial_endpoint
        self._endpoint_policy = endpoint_policy
        self._server_supervision_policy = server_supervision_policy
        self._tracking_supervision_policy = tracking_supervision_policy
        self._transport_supervision_policy = transport_supervision_policy

    def build_minimal(self) -> AdbRuntime:
        """Build a runtime with lifecycle capabilities but no supervision automation."""

        core = self._build_core()
        try:
            return AdbRuntime(
                core.server_state,
                core.inventory_state,
                core.controller,
                core.provisioner,
                transport_supervision_policy=self._transport_supervision_policy,
            )
        except BaseException:
            core.controller.stop(core.initial_server)
            raise

    def build_managed(
        self,
        *,
        event_bus: EventBus,
        scheduler: TemporalScheduler[object],
        transport_ensurer: AdbTransportEnsurer | None = None,
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
        recovery_policy = self._transport_supervision_policy.recovery_ensure_policy
        if (
            configured_transports
            and recovery_policy is not None
            and not isinstance(transport_ensurer, AdbTransportEnsurer)
        ):
            raise TypeError(
                "transport_ensurer must satisfy AdbTransportEnsurer when configured transport recovery is enabled"
            )
        if transport_ensurer is not None and not isinstance(
            transport_ensurer, AdbTransportEnsurer
        ):
            raise TypeError("transport_ensurer must satisfy AdbTransportEnsurer or be None")

        core = self._build_core()
        try:
            server_supervisor = AdbServerSupervisor(
                core.server_state,
                core.controller,
                core.provisioner,
                event_bus,
                scheduler,
                self._server_supervision_policy,
            )
            tracking_supervisor = (
                AdbDevicesTrackingSupervisor(
                    core.initial_server,
                    event_bus,
                    self._tracking_supervision_policy,
                    inventory_state=core.inventory_state,
                )
                if track_devices
                else None
            )
            transport_supervisor = (
                AdbConfiguredTransportSupervisor(
                    core.initial_server,
                    event_bus,
                    transport_ensurer,
                    inventory=core.inventory_state,
                )
                if configured_transports
                else None
            )
            return AdbRuntime(
                core.server_state,
                core.inventory_state,
                core.controller,
                core.provisioner,
                event_bus=event_bus,
                server_supervisor=server_supervisor,
                tracking_supervisor=tracking_supervisor,
                transport_supervisor=transport_supervisor,
                transport_supervision_policy=self._transport_supervision_policy,
            )
        except BaseException:
            core.controller.stop(core.initial_server)
            raise

    def _build_core(self) -> _BootstrapCore:
        issuer = AdbServerEpochSequence()
        controller = self._server_controller_factory(issuer)
        if not isinstance(controller, AdbServerController):
            raise TypeError("server controller factory must return AdbServerController")

        initial_constraint = self._initial_endpoint
        if initial_constraint is None and isinstance(
            self._endpoint_policy, AdbServerFixedEndpoint
        ):
            initial_constraint = self._endpoint_policy.endpoint

        initial_server = controller.provide(initial_constraint)
        if not isinstance(initial_server, AdbServer):
            raise TypeError("server controller provide() must return AdbServer")
        try:
            provision_endpoint = resolve_server_provisioning_endpoint(
                self._endpoint_policy,
                initial_server.endpoint,
            )
            provisioner = AdbServerControllerProvisioner(
                controller,
                provision_endpoint,
            )
            return _BootstrapCore(
                controller=controller,
                provisioner=provisioner,
                server_state=AdbServerState(initial_server),
                inventory_state=AdbDevicesInventoryState(),
                initial_server=initial_server,
            )
        except BaseException:
            controller.stop(initial_server)
            raise


def _is_event_bus(value: object) -> bool:
    return (
        callable(getattr(value, "publish", None))
        and callable(getattr(value, "subscribe", None))
        and callable(getattr(value, "unsubscribe", None))
    )


__all__ = ["AdbRuntimeBootstrap"]
