from __future__ import annotations

import re

from adb.errors import AdbProtocolError
from android.display import (
    AndroidDisplayId,
    AndroidDisplayRotation,
    AndroidDisplayState,
    AndroidDisplaysSnapshot,
    AndroidPhysicalDisplayId,
    AndroidPhysicalDisplayState,
    AndroidPhysicalDisplaysSnapshot,
)
from geometry import Rect


_DISPLAY_SECTION_RE = re.compile(r"^\s*Display\s+(\d+):\s*$")
_DISPLAY_ID_RE = re.compile(r"\bdisplayId\s+(\d+)\b")
_LEGACY_DISPLAY_ID_RE = re.compile(r"\bmDisplayId=(\d+)\b")
_REAL_SIZE_RE = re.compile(r"\breal\s+(\d+)\s*x\s*(\d+)\b")
_ROTATION_RE = re.compile(r"\brotation\s*(\d+)\b")
_DENSITY_RE = re.compile(r"\bdensity\s+(\d+)\b")
_LOCAL_UNIQUE_ID_RE = re.compile(r'\buniqueId\s+"local:(\d+)"')
_PHYSICAL_DISPLAY_RE = re.compile(
    r'^\s*Display\s+(\d+)(?:\s+\(HWC display\s+(\d+)\))?(?::\s*(.*))?$'
)
_PORT_RE = re.compile(r"\bport=(\d+)\b")
_PNP_RE = re.compile(r"\bpnpId=([^\s]+)")
_DISPLAY_NAME_RE = re.compile(r'\bdisplayName="([^"]*)"')


def parse_dumpsys_display(text: str) -> AndroidDisplaysSnapshot:
    """Parse supported AOSP logical ``DisplayInfo`` rows and reject partial parses."""
    if not isinstance(text, str):
        raise TypeError("display dump must be a string")

    current_display_id: int | None = None
    by_id: dict[int, AndroidDisplayState] = {}
    priority: dict[int, int] = {}
    candidate_rows = 0

    for line in text.splitlines():
        section = _DISPLAY_SECTION_RE.match(line)
        if section is not None:
            current_display_id = int(section.group(1))
            continue

        legacy_id = _LEGACY_DISPLAY_ID_RE.search(line)
        if legacy_id is not None:
            current_display_id = int(legacy_id.group(1))

        if "DisplayInfo{" not in line or "mOverrideDisplayInfo=null" in line:
            continue
        candidate_rows += 1

        embedded_id = _DISPLAY_ID_RE.search(line)
        display_id = int(embedded_id.group(1)) if embedded_id else current_display_id
        size = _REAL_SIZE_RE.search(line)
        rotation = _ROTATION_RE.search(line)
        density = _DENSITY_RE.search(line)
        missing = [
            name
            for name, value in (
                ("display id", display_id),
                ("real size", size),
                ("rotation", rotation),
                ("density", density),
            )
            if value is None
        ]
        if missing:
            raise AdbProtocolError(
                "unsupported dumpsys display candidate row did not match the supported "
                "Android display format: missing " + ", ".join(missing)
            )
        assert display_id is not None
        assert size is not None
        assert rotation is not None
        assert density is not None

        try:
            normalized_rotation = AndroidDisplayRotation.from_surface_rotation(
                int(rotation.group(1))
            )
        except ValueError as exc:
            raise AdbProtocolError(
                f"Android display {display_id} has unsupported rotation {rotation.group(1)}"
            ) from exc

        local_id = _LOCAL_UNIQUE_ID_RE.search(line)
        state = AndroidDisplayState(
            display_id=AndroidDisplayId(display_id),
            bounds=Rect(
                x=0,
                y=0,
                width=int(size.group(1)),
                height=int(size.group(2)),
            ),
            rotation=normalized_rotation,
            density_dpi=int(density.group(1)),
            physical_display_id=(
                AndroidPhysicalDisplayId(int(local_id.group(1))) if local_id else None
            ),
        )
        row_priority = 2 if "mOverrideDisplayInfo=" in line else 1
        if display_id not in by_id or row_priority >= priority[display_id]:
            by_id[display_id] = state
            priority[display_id] = row_priority

    if candidate_rows == 0:
        raise AdbProtocolError(
            "unsupported dumpsys display format: no DisplayInfo rows were present"
        )
    if not by_id:
        raise AdbProtocolError("unsupported dumpsys display format")
    return AndroidDisplaysSnapshot(tuple(by_id[key] for key in sorted(by_id)))


def parse_surfaceflinger_display_ids(text: str) -> AndroidPhysicalDisplaysSnapshot:
    if not isinstance(text, str):
        raise TypeError("SurfaceFlinger display dump must be a string")

    displays: list[AndroidPhysicalDisplayState] = []
    for line in text.splitlines():
        match = _PHYSICAL_DISPLAY_RE.match(line)
        if match is None:
            continue
        suffix = match.group(3) or ""
        port = _PORT_RE.search(suffix)
        pnp = _PNP_RE.search(suffix)
        name = _DISPLAY_NAME_RE.search(suffix)
        displays.append(
            AndroidPhysicalDisplayState(
                display_id=AndroidPhysicalDisplayId(int(match.group(1))),
                hwc_display_id=(
                    int(match.group(2)) if match.group(2) is not None else None
                ),
                port=int(port.group(1)) if port else None,
                pnp_id=pnp.group(1) if pnp else None,
                display_name=name.group(1) if name else None,
            )
        )

    if not displays:
        raise AdbProtocolError(
            "unsupported SurfaceFlinger --display-id format: no physical display rows"
        )
    return AndroidPhysicalDisplaysSnapshot(tuple(displays))


__all__ = ["parse_dumpsys_display", "parse_surfaceflinger_display_ids"]
