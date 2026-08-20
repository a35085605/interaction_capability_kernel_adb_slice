from __future__ import annotations

import re

from adb.errors import AdbProtocolError
from android.application import (
    AndroidPackageEnabledState,
    AndroidPackageState,
    AndroidResumedActivitiesSnapshot,
    AndroidResumedActivity,
)
from android.display import AndroidDisplayId
from android.identity import AndroidComponentName, AndroidPackageName, AndroidUserId
from android.platform import AndroidBuildInfo
from android.runtime import (
    AndroidBootState,
    AndroidKeyguardState,
    AndroidPowerState,
    AndroidPowerWakefulness,
    AndroidUserInfo,
    AndroidUserPhase,
    AndroidUsersSnapshot,
    AndroidUserState,
)


_USER_STATE_RE = re.compile(r"\bid=(\d+),\s*state=([A-Z_]+)\b")
_USER_NOT_STARTED_RE = re.compile(r"^\s*User is not started:\s*(\d+)\s*$")
_ACTIVITY_DISPLAY_RE = re.compile(r"^\s*Display\s+#(\d+)\b")
_RESUMED_ACTIVITY_RE = re.compile(
    r"(?:mResumedActivity:\s*|topResumedActivity=)"
    r"ActivityRecord\{[^}]*?\bu(\d+)\s+"
    r"([A-Za-z][A-Za-z0-9_.]*)/(\.?[A-Za-z_$][A-Za-z0-9_.$]*)\s+t(\d+)\b"
)
_WAKEFULNESS_RE = re.compile(r"\bmWakefulness=(Awake|Asleep|Dozing|Dreaming)\b")
_WAKEFULNESS_ALT_RE = re.compile(
    r"\bWakefulness:\s*(Awake|Asleep|Dozing|Dreaming)\b"
)
_KEYGUARD_SHOWING_RE = re.compile(
    r"\bmKeyguardShowing=(true|false)\b", re.IGNORECASE
)
_USERS_HEADER_RE = re.compile(r"^\s*(\d+)\s+users?:\s*$")
_USER_ROW_RE = re.compile(
    r"^\s*\d+:\s+id=(\d+),\s*name=(.*?),\s*type=([^,]+),\s*flags=([^\s(]*)(.*)$"
)
_PARENT_ID_RE = re.compile(r"\(parentId=(\d+)\)")
_PACKAGE_USER_RE = re.compile(r"^\s*User\s+(\d+):\s*(.*)$")
_PACKAGE_BOOL_RE = re.compile(r"\b(installed|hidden|suspended)=(true|false)\b")
_PACKAGE_ENABLED_RE = re.compile(r"\benabled=(\d+)\b")


def parse_boot_completed(text: str) -> AndroidBootState:
    if not isinstance(text, str):
        raise TypeError("boot-completed value must be a string")
    value = text.strip()
    if value == "1":
        return AndroidBootState.BOOTED
    if value in {"", "0"}:
        return AndroidBootState.BOOTING
    raise AdbProtocolError(f"unsupported sys.boot_completed value {value!r}")


def parse_build_info(text: str) -> AndroidBuildInfo:
    if not isinstance(text, str):
        raise TypeError("Android build-info output must be a string")
    lines = [line.strip() for line in text.splitlines()]
    if len(lines) != 3 or any(not line for line in lines):
        raise AdbProtocolError("unsupported Android build-info output")
    try:
        return AndroidBuildInfo(
            sdk_int=int(lines[0]), release=lines[1], fingerprint=lines[2]
        )
    except (TypeError, ValueError) as exc:
        raise AdbProtocolError("unsupported Android build-info output") from exc


def parse_current_user(text: str) -> AndroidUserId:
    if not isinstance(text, str):
        raise TypeError("current-user output must be a string")
    value = text.strip()
    if re.fullmatch(r"\d+", value) is None:
        raise AdbProtocolError("unsupported current-user output")
    return AndroidUserId(int(value))


def parse_users(text: str) -> AndroidUsersSnapshot:
    if not isinstance(text, str):
        raise TypeError("user-list output must be a string")
    declared: int | None = None
    users: list[AndroidUserInfo] = []
    candidate_rows = 0

    for line in text.splitlines():
        header = _USERS_HEADER_RE.match(line)
        if header:
            declared = int(header.group(1))
            continue
        if "id=" not in line:
            continue
        candidate_rows += 1
        match = _USER_ROW_RE.match(line)
        if match is None:
            raise AdbProtocolError("unsupported verbose Android user row")
        tail = match.group(5)
        parent = _PARENT_ID_RE.search(tail)
        users.append(
            AndroidUserInfo(
                user_id=AndroidUserId(int(match.group(1))),
                name=match.group(2),
                user_type=match.group(3),
                flags=frozenset(
                    part for part in match.group(4).split("|") if part
                ),
                profile_group_id=(
                    AndroidUserId(int(parent.group(1))) if parent else None
                ),
                running="(running)" in tail,
                current="(current)" in tail,
                visible="(visible)" in tail,
            )
        )

    if declared is None:
        raise AdbProtocolError("unsupported verbose Android user-list format")
    if candidate_rows != declared or len(users) != declared:
        raise AdbProtocolError("Android user-list snapshot is incomplete")
    return AndroidUsersSnapshot(tuple(users))


def parse_android_user_state(
    text: str, user_id: AndroidUserId
) -> AndroidUserState | None:
    if not isinstance(user_id, AndroidUserId):
        raise TypeError("user_id must be AndroidUserId")
    not_started = _USER_NOT_STARTED_RE.match(text.strip())
    if not_started is not None:
        if int(not_started.group(1)) != user_id.value:
            raise AdbProtocolError(
                "Android user-state response referenced a different user"
            )
        return None

    match = _USER_STATE_RE.search(text)
    if match is None or int(match.group(1)) != user_id.value:
        raise AdbProtocolError("unsupported Android started-user state format")
    try:
        return AndroidUserState(
            user_id=user_id, phase=AndroidUserPhase[match.group(2)]
        )
    except KeyError as exc:
        raise AdbProtocolError(
            f"unsupported Android user phase {match.group(2)!r}"
        ) from exc


def parse_package_state(
    text: str,
    user_id: AndroidUserId,
    package_name: AndroidPackageName,
) -> AndroidPackageState:
    if not isinstance(user_id, AndroidUserId):
        raise TypeError("user_id must be AndroidUserId")
    if not isinstance(package_name, AndroidPackageName):
        raise TypeError("package_name must be AndroidPackageName")
    if "Unable to find package" in text or "was not found" in text:
        return AndroidPackageState(
            user_id=user_id, package_name=package_name, installed=False
        )

    for line in text.splitlines():
        match = _PACKAGE_USER_RE.match(line)
        if not match or int(match.group(1)) != user_id.value:
            continue
        fields = {
            key: value.lower() == "true"
            for key, value in _PACKAGE_BOOL_RE.findall(match.group(2))
        }
        enabled = _PACKAGE_ENABLED_RE.search(match.group(2))
        if not {"installed", "hidden", "suspended"}.issubset(fields) or enabled is None:
            raise AdbProtocolError(
                "unsupported PackageManager per-user package state"
            )
        try:
            enabled_state = AndroidPackageEnabledState(int(enabled.group(1)))
        except ValueError as exc:
            raise AdbProtocolError("unsupported PackageManager enabled state") from exc
        return AndroidPackageState(
            user_id=user_id,
            package_name=package_name,
            installed=fields["installed"],
            hidden=fields["hidden"],
            suspended=fields["suspended"],
            enabled_state=enabled_state,
        )
    raise AdbProtocolError("package dump did not contain requested user state")


def parse_launcher_component(
    text: str, package_name: AndroidPackageName
) -> AndroidComponentName | None:
    if not isinstance(package_name, AndroidPackageName):
        raise TypeError("package_name must be AndroidPackageName")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or all("No activity found" in line for line in lines):
        return None
    candidates = [
        line
        for line in lines
        if re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_.]*/\.?[A-Za-z_$][A-Za-z0-9_.$]*", line
        )
    ]
    if len(candidates) != 1:
        raise AdbProtocolError("unsupported launcher-activity resolution output")
    package, class_name = candidates[0].split("/", 1)
    if package != package_name.value:
        raise AdbProtocolError("launcher resolution returned a different package")
    return AndroidComponentName(package_name, class_name)


def parse_resumed_activities(text: str) -> AndroidResumedActivitiesSnapshot:
    if not isinstance(text, str):
        raise TypeError("activity dump must be a string")
    display_id: AndroidDisplayId | None = None
    activities: list[AndroidResumedActivity] = []
    recognized_structure = "ACTIVITY MANAGER ACTIVITIES" in text
    candidate_rows = 0

    for line in text.splitlines():
        display_match = _ACTIVITY_DISPLAY_RE.match(line)
        if display_match is not None:
            display_id = AndroidDisplayId(int(display_match.group(1)))
            recognized_structure = True
            continue
        if "mResumedActivity:" not in line and "topResumedActivity=" not in line:
            continue
        candidate_rows += 1
        resumed = _RESUMED_ACTIVITY_RE.search(line)
        if resumed is None:
            raise AdbProtocolError("unsupported resumed-activity row format")
        if display_id is None:
            raise AdbProtocolError(
                "resumed activity row was not scoped by a logical display section"
            )
        package = AndroidPackageName(resumed.group(2))
        activities.append(
            AndroidResumedActivity(
                user_id=AndroidUserId(int(resumed.group(1))),
                display_id=display_id,
                component=AndroidComponentName(package, resumed.group(3)),
                task_id=int(resumed.group(4)),
            )
        )

    if not recognized_structure:
        raise AdbProtocolError("unsupported dumpsys activity activities format")
    if candidate_rows != len(activities):
        raise AdbProtocolError("resumed-activity snapshot is incomplete")
    return AndroidResumedActivitiesSnapshot(tuple(activities))


def parse_power_state(text: str) -> AndroidPowerState:
    match = _WAKEFULNESS_RE.search(text) or _WAKEFULNESS_ALT_RE.search(text)
    if match is None:
        raise AdbProtocolError("unsupported dumpsys power wakefulness format")
    mapping = {
        "Awake": AndroidPowerWakefulness.AWAKE,
        "Asleep": AndroidPowerWakefulness.ASLEEP,
        "Dozing": AndroidPowerWakefulness.DOZING,
        "Dreaming": AndroidPowerWakefulness.DREAMING,
    }
    return AndroidPowerState(mapping[match.group(1)])


def parse_keyguard_state(text: str) -> AndroidKeyguardState:
    match = _KEYGUARD_SHOWING_RE.search(text)
    if match is None:
        raise AdbProtocolError("unsupported dumpsys window policy keyguard format")
    return AndroidKeyguardState(showing=match.group(1).lower() == "true")


__all__ = [
    "parse_android_user_state",
    "parse_boot_completed",
    "parse_build_info",
    "parse_current_user",
    "parse_keyguard_state",
    "parse_launcher_component",
    "parse_package_state",
    "parse_power_state",
    "parse_resumed_activities",
    "parse_users",
]
