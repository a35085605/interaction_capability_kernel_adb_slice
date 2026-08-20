from __future__ import annotations

import re

from adb.errors import AdbProtocolError
from android.display import AndroidDisplayId
from android.identity import AndroidComponentName, AndroidPackageName, AndroidUserId
from android.window import (
    AndroidDisplayOcclusion,
    AndroidDisplayOcclusionKind,
    AndroidDisplayOcclusionsSnapshot,
    AndroidWindowId,
    AndroidWindowState,
    AndroidWindowsSnapshot,
    AndroidWindowViewVisibility,
)
from geometry import Rect


_WINDOW_HEADER_RE = re.compile(
    r"^\s*Window #\d+ Window\{([0-9A-Fa-f]+)\s+u(\d+)\s+([^}]+)\}:\s*$"
)
_CURRENT_FOCUS_RE = re.compile(r"\bmCurrentFocus=Window\{([0-9A-Fa-f]+)\b")
_WINDOW_DISPLAY_RE = re.compile(r"\bmDisplayId=(\d+)\b")
_WINDOW_FRAME_RE = re.compile(
    r"\b(?:frame|mFrame)=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]"
)
_WINDOW_VIS_RE = re.compile(r"\bmViewVisibility=(0x[0-9A-Fa-f]+|\d+)\b")
_WINDOW_SURFACE_RE = re.compile(r"\bmHasSurface=(true|false)\b", re.IGNORECASE)
_WINDOW_PACKAGE_RE = re.compile(r"\bpackage=([A-Za-z][A-Za-z0-9_.]*)\b")
_WINDOW_MODE_RE = re.compile(r"\b(?:mWindowingMode|windowingMode)=([^\s,}]+)")
_WM_DISPLAY_SECTION_RES = (
    re.compile(r"^\s*Display\s+#(\d+)\b"),
    re.compile(r"^\s*Display:\s*mDisplayId=(\d+)\b"),
    re.compile(r"^\s*DisplayContent\{[^}]*\bdisplayId=(\d+)\b"),
)
_INSETS_SOURCE_CANDIDATE_RE = re.compile(r"\bInsetsSource\b")
_INSETS_TYPE_RE = re.compile(r"\b(?:type|mType)=([^\s,}]+)")
_INSETS_FRAME_RE = re.compile(
    r"\b(?:frame|mFrame)=\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]"
)
_INSETS_VISIBLE_RE = re.compile(
    r"\b(?:visible|mVisible)=(true|false)\b", re.IGNORECASE
)


def _rect_from_match(match: re.Match[str], *, context: str) -> Rect:
    try:
        return Rect.from_ltrb(
            left=int(match.group(1)),
            top=int(match.group(2)),
            right=int(match.group(3)),
            bottom=int(match.group(4)),
        )
    except ValueError as exc:
        raise AdbProtocolError(f"invalid {context} rectangle") from exc


def parse_windows(text: str) -> AndroidWindowsSnapshot:
    if not isinstance(text, str):
        raise TypeError("window dump must be a string")
    focus_match = _CURRENT_FOCUS_RE.search(text)
    focused_id = focus_match.group(1) if focus_match else None
    lines = text.splitlines()
    windows: list[AndroidWindowState] = []
    i = 0

    while i < len(lines):
        header = _WINDOW_HEADER_RE.match(lines[i])
        if header is None:
            i += 1
            continue
        candidate_id = header.group(1)
        user = int(header.group(2))
        title = header.group(3).strip()
        block = [lines[i]]
        i += 1
        while i < len(lines) and _WINDOW_HEADER_RE.match(lines[i]) is None:
            block.append(lines[i])
            i += 1
        body = "\n".join(block)

        display = _WINDOW_DISPLAY_RE.search(body)
        frame = _WINDOW_FRAME_RE.search(body)
        visibility = _WINDOW_VIS_RE.search(body)
        surface = _WINDOW_SURFACE_RE.search(body)
        if None in (display, frame, visibility, surface):
            raise AdbProtocolError(
                f"unsupported WindowState row for {candidate_id}: required facts missing"
            )
        assert display is not None
        assert frame is not None
        assert visibility is not None
        assert surface is not None

        raw_visibility = int(visibility.group(1), 0)
        try:
            view_visibility = AndroidWindowViewVisibility(raw_visibility)
        except ValueError as exc:
            raise AdbProtocolError(
                f"unsupported Android window visibility {raw_visibility}"
            ) from exc

        package_match = _WINDOW_PACKAGE_RE.search(body)
        package_name: AndroidPackageName | None = None
        component: AndroidComponentName | None = None
        component_match = re.search(
            r"([A-Za-z][A-Za-z0-9_.]*)/(\.?[A-Za-z_$][A-Za-z0-9_.$]*)",
            title,
        )
        if component_match:
            package_name = AndroidPackageName(component_match.group(1))
            component = AndroidComponentName(package_name, component_match.group(2))
        elif package_match:
            package_name = AndroidPackageName(package_match.group(1))

        mode = _WINDOW_MODE_RE.search(body)
        windows.append(
            AndroidWindowState(
                window_id=AndroidWindowId(candidate_id),
                user_id=AndroidUserId(user),
                display_id=AndroidDisplayId(int(display.group(1))),
                bounds=_rect_from_match(frame, context="Android window"),
                view_visibility=view_visibility,
                has_surface=surface.group(1).lower() == "true",
                focused=candidate_id == focused_id,
                package_name=package_name,
                component=component,
                windowing_mode=mode.group(1) if mode else None,
            )
        )

    if not windows:
        raise AdbProtocolError(
            "unsupported dumpsys window windows format: no WindowState rows"
        )
    if focused_id is not None and all(
        window.window_id.value != focused_id for window in windows
    ):
        raise AdbProtocolError(
            "focused window was not present in parsed window snapshot"
        )
    return AndroidWindowsSnapshot(tuple(windows))


def _occlusion_kind(raw_type: str) -> AndroidDisplayOcclusionKind | None:
    normalized = raw_type.lower().replace("_", "").replace("-", "")
    if "statusbar" in normalized:
        return AndroidDisplayOcclusionKind.STATUS_BAR
    if "navigationbar" in normalized or "navbar" in normalized:
        return AndroidDisplayOcclusionKind.NAVIGATION_BAR
    if "displaycutout" in normalized or "cutout" in normalized:
        return AndroidDisplayOcclusionKind.DISPLAY_CUTOUT
    if normalized in {"ime", "itypeime"} or normalized.endswith("ime"):
        return AndroidDisplayOcclusionKind.IME
    return None


def parse_display_occlusions(
    text: str, display_id: AndroidDisplayId
) -> AndroidDisplayOcclusionsSnapshot | None:
    if not isinstance(display_id, AndroidDisplayId):
        raise TypeError("display_id must be AndroidDisplayId")
    if not isinstance(text, str):
        raise TypeError("window-display dump must be a string")

    current_display: int | None = None
    seen_displays: set[int] = set()
    occlusions: list[AndroidDisplayOcclusion] = []

    for line in text.splitlines():
        section = next(
            (
                match
                for pattern in _WM_DISPLAY_SECTION_RES
                if (match := pattern.match(line)) is not None
            ),
            None,
        )
        if section is not None:
            current_display = int(section.group(1))
            seen_displays.add(current_display)
            continue
        if (
            current_display != display_id.value
            or _INSETS_SOURCE_CANDIDATE_RE.search(line) is None
        ):
            continue

        type_match = _INSETS_TYPE_RE.search(line)
        if type_match is None:
            raise AdbProtocolError("InsetsSource row did not expose a type")
        kind = _occlusion_kind(type_match.group(1))
        if kind is None:
            continue
        frame = _INSETS_FRAME_RE.search(line)
        visible = _INSETS_VISIBLE_RE.search(line)
        if frame is None or visible is None:
            raise AdbProtocolError(
                "supported InsetsSource row is missing frame/visibility"
            )
        occlusions.append(
            AndroidDisplayOcclusion(
                kind=kind,
                bounds=_rect_from_match(frame, context="Android inset"),
                visible=visible.group(1).lower() == "true",
            )
        )

    if not seen_displays:
        raise AdbProtocolError("unsupported dumpsys window displays format")
    if display_id.value not in seen_displays:
        return None
    return AndroidDisplayOcclusionsSnapshot(
        display_id=display_id, occlusions=tuple(occlusions)
    )


__all__ = ["parse_display_occlusions", "parse_windows"]
