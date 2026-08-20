from __future__ import annotations

from adb.server import AdbServerEndpoint
from adb.transport import AdbTransportSelector
from android.adb.adapters._attempt import ClientFactory, default_client_factory, shell_v2_attempt
from android.command import AndroidActivityLaunch, AndroidPackageForceStop
from native_attempt import NativeAttemptResult


class _AndroidAdbCommandAdapter:
    backend_id = "android-adb-command"

    def __init__(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        *,
        _client_factory: ClientFactory = default_client_factory,
    ) -> None:
        if not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")
        self.endpoint = endpoint
        self.selector = selector
        self._client_factory = _client_factory

    def _attempt(self, command: str) -> NativeAttemptResult:
        return shell_v2_attempt(
            self.endpoint,
            self.selector,
            command,
            backend_id=self.backend_id,
            client_factory=self._client_factory,
        )


class AdbActivityLauncher(_AndroidAdbCommandAdapter):
    """Typed ``am start`` attempt with explicit Android user/component identity."""

    def launch(self, operation: AndroidActivityLaunch) -> NativeAttemptResult:
        if not isinstance(operation, AndroidActivityLaunch):
            raise TypeError("operation must be AndroidActivityLaunch")
        return self._attempt(
            "am start --user "
            f"{operation.user_id.value} -n {operation.component.flattened}"
        )


class AdbPackageForceStopper(_AndroidAdbCommandAdapter):
    """Typed ``am force-stop`` attempt with explicit Android user/package identity."""

    def force_stop(self, operation: AndroidPackageForceStop) -> NativeAttemptResult:
        if not isinstance(operation, AndroidPackageForceStop):
            raise TypeError("operation must be AndroidPackageForceStop")
        return self._attempt(
            "am force-stop --user "
            f"{operation.user_id.value} {operation.package_name.value}"
        )


__all__ = ["AdbActivityLauncher", "AdbPackageForceStopper"]
