from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from adb.epoch import EpochIssuer
from adb.runtime.core import AdbRuntime
from networking import TcpAddress
from adb.server.epoch import ServerEpochSequence
from adb.server.lifecycle.control.backend import AdbServerBackend
from adb.server.lifecycle.control.provisioner import AdbServerProvisioner
from adb.server.lifecycle.control.retirer import AdbServerRetirer
from adb.server.lifecycle.control.subprocess import SubprocessAdbServerBackend
from adb.server.lifecycle.supervision.policy import AdbServerSupervisionPolicy
from adb.server.state import AdbServerState
from adb.runtime.state import AdbRuntimeState
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


_AdbServerBackendFactory = Callable[[], AdbServerBackend]


def _default_server_backend_factory() -> AdbServerBackend:
    return SubprocessAdbServerBackend()


@dataclass(frozen=True, slots=True)
class _BootstrapCore:
    server_backend: AdbServerBackend
    server_epoch_issuer: ServerEpochSequence
    server_provisioner: AdbServerProvisioner
    server_retirer: AdbServerRetirer
    runtime_state: AdbRuntimeState
    devices_snapshot_epoch_issuer: EpochIssuer[AdbDevicesSnapshotEpoch]


class AdbRuntimeBootstrap:
    """Build fresh runtime-scoped ADB object graphs from retained composition configuration."""

    def __init__(
        self,
        *,
        server_backend_factory: _AdbServerBackendFactory | None = None,
        endpoint: TcpAddress | None = None,
        pin_endpoint: bool = True,
        server_recovery_enabled: bool = True,
        server_supervision_policy: AdbServerSupervisionPolicy | None = None,
        tracking_supervision_policy: AdbDevicesTrackingSupervisionPolicy | None = None,
        transport_supervision_policy: AdbConfiguredTransportSupervisionPolicy | None = None,
    ) -> None:
        if server_backend_factory is not None and not callable(server_backend_factory):
            raise TypeError("server_backend_factory must be callable or None")
        if server_backend_factory is None:
            server_backend_factory = _default_server_backend_factory
        if endpoint is not None and not isinstance(endpoint, TcpAddress):
            raise TypeError("endpoint must be TcpAddress or None")
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
        runtime: AdbRuntime | None = None
        try:
            runtime = self._build_runtime(
                core.runtime_state,
                server_epoch_issuer=core.server_epoch_issuer,
                server_provisioner=core.server_provisioner,
                server_retirer=core.server_retirer,
                transport_supervision_policy=self._transport_supervision_policy,
                _bootstrap_server=True,
            )
            self._configure_recovery_provisioner(runtime, core)
            return runtime
        except BaseException:
            if runtime is not None:
                self._dispose_failed_runtime(runtime)
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
        runtime: AdbRuntime | None = None
        try:
            runtime = self._build_runtime(
                core.runtime_state,
                server_epoch_issuer=core.server_epoch_issuer,
                server_provisioner=core.server_provisioner,
                server_retirer=core.server_retirer,
                event_bus=event_bus,
                server_supervision_scheduler=scheduler,
                server_supervision_policy=self._server_supervision_policy,
                server_recovery_enabled=self._server_recovery_enabled,
                transport_supervision_policy=self._transport_supervision_policy,
                _bootstrap_server=True,
            )
            self._configure_recovery_provisioner(runtime, core)
            initial_server = runtime.server
            if initial_server is None:
                raise RuntimeError("bootstrapped ADB runtime has no initial server")

            tracking_supervisor = (
                AdbDevicesTrackingSupervisor(
                    initial_server,
                    event_bus,
                    self._tracking_supervision_policy,
                    server_state=core.runtime_state.server,
                    devices_snapshot_epoch_issuer=core.devices_snapshot_epoch_issuer,
                    snapshot_state=core.runtime_state.devices,
                )
                if track_devices
                else None
            )
            transport_supervisor = (
                AdbConfiguredTransportSupervisor(
                    initial_server,
                    event_bus,
                    tcp_transport_ensurer,
                    server_state=core.runtime_state.server,
                    devices=core.runtime_state.devices,
                )
                if configured_transports
                else None
            )
            runtime._install_auxiliary_supervisors(
                tracking_supervisor,
                transport_supervisor,
            )
            return runtime
        except BaseException:
            if runtime is not None:
                self._dispose_failed_runtime(runtime)
            raise

    def _build_runtime(self, *args: object, **kwargs: object) -> AdbRuntime:
        return AdbRuntime(*args, **kwargs)

    def _build_core(self) -> _BootstrapCore:
        server_epoch_issuer = ServerEpochSequence()
        devices_snapshot_epoch_issuer = AdbDevicesSnapshotEpochSequence()
        backend = self._server_backend_factory()
        if not isinstance(backend, AdbServerBackend):
            raise TypeError("server backend factory must return AdbServerBackend")

        return _BootstrapCore(
            server_backend=backend,
            server_epoch_issuer=server_epoch_issuer,
            server_provisioner=AdbServerProvisioner(
                backend,
                endpoint=self._endpoint,
            ),
            server_retirer=AdbServerRetirer(backend),
            runtime_state=AdbRuntimeState(
                server=AdbServerState(),
                devices=AdbDevicesSnapshotState(),
            ),
            devices_snapshot_epoch_issuer=devices_snapshot_epoch_issuer,
        )

    def _configure_recovery_provisioner(
        self,
        runtime: AdbRuntime,
        core: _BootstrapCore,
    ) -> None:
        initial_server = runtime.server
        if initial_server is None:
            raise RuntimeError("bootstrapped ADB runtime has no initial server")
        recovery_endpoint = initial_server.endpoint if self._pin_endpoint else None
        runtime._replace_server_provisioner(
            AdbServerProvisioner(
                core.server_backend,
                endpoint=recovery_endpoint,
            )
        )

    @staticmethod
    def _dispose_failed_runtime(runtime: AdbRuntime) -> None:
        runtime.close()
        runtime.retire_server()


def _is_event_bus(value: object) -> bool:
    return (
        callable(getattr(value, "publish", None))
        and callable(getattr(value, "subscribe", None))
        and callable(getattr(value, "unsubscribe", None))
    )


__all__ = ["AdbRuntimeBootstrap"]
