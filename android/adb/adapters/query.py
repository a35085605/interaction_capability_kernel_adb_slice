from __future__ import annotations

from collections.abc import Callable

from adb._internal.client import AdbServiceClient
from adb.errors import AdbProtocolError, AdbRemoteCommandError
from adb.server import AdbServerEndpoint
from adb.transport import AdbTransportSelector
from android.adb.adapters._display_parsers import (
    parse_dumpsys_display,
    parse_surfaceflinger_display_ids,
)
from android.adb.adapters._runtime_parsers import (
    parse_android_user_state,
    parse_boot_completed,
    parse_build_info,
    parse_current_user,
    parse_keyguard_state,
    parse_launcher_component,
    parse_package_state,
    parse_power_state,
    parse_resumed_activities,
    parse_users,
)
from android.adb.adapters._window_parsers import (
    parse_display_occlusions,
    parse_windows,
)
from android.application import AndroidPackageState, AndroidResumedActivitiesSnapshot
from android.display import (
    AndroidDisplayId,
    AndroidDisplayState,
    AndroidDisplaysSnapshot,
    AndroidPhysicalDisplaysSnapshot,
)
from android.identity import AndroidComponentName, AndroidPackageName, AndroidUserId
from android.platform import AndroidBuildInfo
from android.runtime import (
    AndroidBootState,
    AndroidKeyguardState,
    AndroidPowerState,
    AndroidUsersSnapshot,
    AndroidUserState,
)
from android.window import (
    AndroidDisplayOcclusionsSnapshot,
    AndroidWindowId,
    AndroidWindowState,
    AndroidWindowsSnapshot,
)


_ClientFactory = Callable[[AdbServerEndpoint], AdbServiceClient]


def _default_client_factory(endpoint: AdbServerEndpoint) -> AdbServiceClient:
    return AdbServiceClient(endpoint)


def _run_text_query(
    endpoint: AdbServerEndpoint,
    selector: AdbTransportSelector,
    *,
    command_name: str,
    command: str,
    client_factory: _ClientFactory,
) -> str:
    result = client_factory(endpoint).shell_v2(selector, command)
    if result.exit_code != 0:
        raise AdbRemoteCommandError(
            command_name=command_name,
            exit_code=result.exit_code,
            stderr=result.stderr,
        )
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdbProtocolError(f"{command_name} output is not valid UTF-8") from exc


class _BaseInspector:
    def __init__(
        self, *, _client_factory: _ClientFactory = _default_client_factory
    ) -> None:
        self._client_factory = _client_factory

    def _query(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        command: str,
    ) -> str:
        return _run_text_query(
            endpoint,
            selector,
            command_name=command,
            command=command,
            client_factory=self._client_factory,
        )


class SmartSocketAdbBootStateInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidBootState:
        return parse_boot_completed(
            self._query(endpoint, selector, "getprop sys.boot_completed")
        )


class SmartSocketAdbBuildInfoInspector(_BaseInspector):
    _COMMAND = (
        "getprop ro.build.version.sdk; getprop ro.build.version.release; "
        "getprop ro.build.fingerprint"
    )

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidBuildInfo:
        return parse_build_info(self._query(endpoint, selector, self._COMMAND))


class SmartSocketAdbDisplaysInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidDisplaysSnapshot:
        return parse_dumpsys_display(self._query(endpoint, selector, "dumpsys display"))


class SmartSocketAdbDisplayInspector:
    def __init__(self, displays_inspector: object | None = None) -> None:
        self.displays_inspector = displays_inspector or SmartSocketAdbDisplaysInspector()
        if not hasattr(self.displays_inspector, "inspect"):
            raise TypeError("displays_inspector must provide inspect()")

    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        display_id: AndroidDisplayId,
    ) -> AndroidDisplayState | None:
        if not isinstance(display_id, AndroidDisplayId):
            raise TypeError("display_id must be AndroidDisplayId")
        snapshot = self.displays_inspector.inspect(endpoint, selector)
        if not isinstance(snapshot, AndroidDisplaysSnapshot):
            raise TypeError("displays inspector must return AndroidDisplaysSnapshot")
        return next(
            (display for display in snapshot.displays if display.display_id == display_id),
            None,
        )


class SmartSocketAdbPhysicalDisplaysInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidPhysicalDisplaysSnapshot:
        return parse_surfaceflinger_display_ids(
            self._query(endpoint, selector, "dumpsys SurfaceFlinger --display-id")
        )


class SmartSocketAdbCurrentUserInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidUserId:
        return parse_current_user(self._query(endpoint, selector, "am get-current-user"))


class SmartSocketAdbUsersInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidUsersSnapshot:
        return parse_users(self._query(endpoint, selector, "cmd user list -v"))


class SmartSocketAdbUserStateInspector(_BaseInspector):
    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        user_id: AndroidUserId,
    ) -> AndroidUserState | None:
        if not isinstance(user_id, AndroidUserId):
            raise TypeError("user_id must be AndroidUserId")
        return parse_android_user_state(
            self._query(endpoint, selector, f"am get-started-user-state {user_id.value}"),
            user_id,
        )


class SmartSocketAdbPackageStateInspector(_BaseInspector):
    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        user_id: AndroidUserId,
        package_name: AndroidPackageName,
    ) -> AndroidPackageState:
        if not isinstance(user_id, AndroidUserId):
            raise TypeError("user_id must be AndroidUserId")
        if not isinstance(package_name, AndroidPackageName):
            raise TypeError("package_name must be AndroidPackageName")
        return parse_package_state(
            self._query(endpoint, selector, f"dumpsys package {package_name.value}"),
            user_id,
            package_name,
        )


class SmartSocketAdbLauncherActivityInspector(_BaseInspector):
    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        user_id: AndroidUserId,
        package_name: AndroidPackageName,
    ) -> AndroidComponentName | None:
        if not isinstance(user_id, AndroidUserId):
            raise TypeError("user_id must be AndroidUserId")
        if not isinstance(package_name, AndroidPackageName):
            raise TypeError("package_name must be AndroidPackageName")
        command = (
            "cmd package resolve-activity --brief "
            f"--user {user_id.value} -a android.intent.action.MAIN "
            "-c android.intent.category.LAUNCHER "
            f"-p {package_name.value}"
        )
        return parse_launcher_component(
            self._query(endpoint, selector, command), package_name
        )


class SmartSocketAdbResumedActivitiesInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidResumedActivitiesSnapshot:
        return parse_resumed_activities(
            self._query(endpoint, selector, "dumpsys activity activities")
        )


class SmartSocketAdbWindowsInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidWindowsSnapshot:
        return parse_windows(self._query(endpoint, selector, "dumpsys window windows"))


class SmartSocketAdbWindowInspector:
    def __init__(self, windows_inspector: object | None = None) -> None:
        self.windows_inspector = windows_inspector or SmartSocketAdbWindowsInspector()
        if not hasattr(self.windows_inspector, "inspect"):
            raise TypeError("windows_inspector must provide inspect()")

    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        window_id: AndroidWindowId,
    ) -> AndroidWindowState | None:
        if not isinstance(window_id, AndroidWindowId):
            raise TypeError("window_id must be AndroidWindowId")
        snapshot = self.windows_inspector.inspect(endpoint, selector)
        if not isinstance(snapshot, AndroidWindowsSnapshot):
            raise TypeError("windows inspector must return AndroidWindowsSnapshot")
        return next(
            (window for window in snapshot.windows if window.window_id == window_id),
            None,
        )


class SmartSocketAdbDisplayOcclusionsInspector(_BaseInspector):
    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        display_id: AndroidDisplayId,
    ) -> AndroidDisplayOcclusionsSnapshot | None:
        if not isinstance(display_id, AndroidDisplayId):
            raise TypeError("display_id must be AndroidDisplayId")
        return parse_display_occlusions(
            self._query(endpoint, selector, "dumpsys window displays"), display_id
        )


class SmartSocketAdbPowerStateInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidPowerState:
        return parse_power_state(self._query(endpoint, selector, "dumpsys power"))


class SmartSocketAdbKeyguardStateInspector(_BaseInspector):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidKeyguardState:
        return parse_keyguard_state(
            self._query(endpoint, selector, "dumpsys window policy")
        )


__all__ = [
    "SmartSocketAdbBootStateInspector",
    "SmartSocketAdbBuildInfoInspector",
    "SmartSocketAdbCurrentUserInspector",
    "SmartSocketAdbDisplayInspector",
    "SmartSocketAdbDisplayOcclusionsInspector",
    "SmartSocketAdbDisplaysInspector",
    "SmartSocketAdbKeyguardStateInspector",
    "SmartSocketAdbLauncherActivityInspector",
    "SmartSocketAdbPackageStateInspector",
    "SmartSocketAdbPhysicalDisplaysInspector",
    "SmartSocketAdbPowerStateInspector",
    "SmartSocketAdbResumedActivitiesInspector",
    "SmartSocketAdbUsersInspector",
    "SmartSocketAdbUserStateInspector",
    "SmartSocketAdbWindowInspector",
    "SmartSocketAdbWindowsInspector",
    "parse_android_user_state",
    "parse_boot_completed",
    "parse_build_info",
    "parse_current_user",
    "parse_display_occlusions",
    "parse_dumpsys_display",
    "parse_keyguard_state",
    "parse_launcher_component",
    "parse_package_state",
    "parse_power_state",
    "parse_resumed_activities",
    "parse_surfaceflinger_display_ids",
    "parse_users",
    "parse_windows",
]
