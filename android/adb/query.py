from __future__ import annotations

from typing import Protocol

from adb.server import AdbServerEndpoint
from adb.transport import AdbTransportSelector
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


class AdbBootStateInspector(Protocol):
    """Read-only Android boot readiness query reached through ADB."""

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidBootState: ...


class AdbBuildInfoInspector(Protocol):
    """Read-only Android build/version facts reached through ADB."""

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidBuildInfo: ...


class AdbDisplaysInspector(Protocol):
    """Read-only listing query for logical displays on one Android device via ADB."""

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidDisplaysSnapshot: ...


class AdbDisplayInspector(Protocol):
    """Read-only Android display query reached through an ADB server."""

    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        display_id: AndroidDisplayId,
    ) -> AndroidDisplayState | None:
        """Return display facts, or ``None`` when the display is not observed."""
        ...


class AdbPhysicalDisplaysInspector(Protocol):
    """SurfaceFlinger physical-display identities available for deterministic capture."""

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidPhysicalDisplaysSnapshot: ...


class AdbCurrentUserInspector(Protocol):
    """Read the Android framework's current user identity."""

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidUserId: ...


class AdbUsersInspector(Protocol):
    """Read the current complete verbose user/profile listing."""

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidUsersSnapshot: ...


class AdbUserStateInspector(Protocol):
    """Per-user Android started-user state."""

    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        user_id: AndroidUserId,
    ) -> AndroidUserState | None: ...


class AdbPackageStateInspector(Protocol):
    """Per-user PackageManager installation/availability facts."""

    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        user_id: AndroidUserId,
        package_name: AndroidPackageName,
    ) -> AndroidPackageState: ...


class AdbLauncherActivityInspector(Protocol):
    """Resolve the launcher activity for one package and user, when one exists."""

    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        user_id: AndroidUserId,
        package_name: AndroidPackageName,
    ) -> AndroidComponentName | None: ...


class AdbResumedActivitiesInspector(Protocol):
    """Current resumed activities scoped by user, logical display, and task."""

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidResumedActivitiesSnapshot: ...


class AdbWindowsInspector(Protocol):
    """Current WindowManager windows in the supported native dump format."""

    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidWindowsSnapshot: ...


class AdbWindowInspector(Protocol):
    """Single native Android window lookup derived from a fresh window snapshot."""

    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        window_id: AndroidWindowId,
    ) -> AndroidWindowState | None: ...


class AdbDisplayOcclusionsInspector(Protocol):
    """Status/navigation/cutout/IME inset sources for one logical display."""

    def inspect(
        self,
        endpoint: AdbServerEndpoint,
        selector: AdbTransportSelector,
        display_id: AndroidDisplayId,
    ) -> AndroidDisplayOcclusionsSnapshot | None: ...


class AdbPowerStateInspector(Protocol):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidPowerState: ...


class AdbKeyguardStateInspector(Protocol):
    def inspect(
        self, endpoint: AdbServerEndpoint, selector: AdbTransportSelector
    ) -> AndroidKeyguardState: ...


__all__ = [
    "AdbBootStateInspector",
    "AdbBuildInfoInspector",
    "AdbCurrentUserInspector",
    "AdbDisplayInspector",
    "AdbDisplayOcclusionsInspector",
    "AdbDisplaysInspector",
    "AdbKeyguardStateInspector",
    "AdbLauncherActivityInspector",
    "AdbPackageStateInspector",
    "AdbPhysicalDisplaysInspector",
    "AdbPowerStateInspector",
    "AdbResumedActivitiesInspector",
    "AdbUsersInspector",
    "AdbUserStateInspector",
    "AdbWindowInspector",
    "AdbWindowsInspector",
]
