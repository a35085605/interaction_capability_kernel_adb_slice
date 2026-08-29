from __future__ import annotations

from adb.aosp.errors import AdbServiceError
from adb.aosp.protocol.smart_socket.services import is_transport_selection_service
from adb.errors import (
    AdbTransportAmbiguousError,
    AdbTransportNotFoundError,
    AdbTransportSelectionError,
    AdbTransportUnavailableError,
)


def translate_transport_selection_error(
    error: AdbServiceError,
) -> AdbTransportSelectionError | None:
    """Translate a native transport-selection FAIL into its domain classification.

    Non-selection service failures are deliberately left untranslated so an ``exec:`` or
    ``shell:`` failure cannot be mistaken for failure to select the transport.
    """

    if not isinstance(error, AdbServiceError):
        raise TypeError("error must be AdbServiceError")
    if not is_transport_selection_service(error.service):
        return None

    detail = error.detail
    lowered = detail.lower()
    if "more than one" in lowered or "multiple devices" in lowered:
        return AdbTransportAmbiguousError(error.service, detail)
    if (
        "not found" in lowered
        or "no devices" in lowered
        or "no device" in lowered
        or "unknown transport" in lowered
    ):
        return AdbTransportNotFoundError(error.service, detail)
    if (
        "offline" in lowered
        or "unauthorized" in lowered
        or "no permissions" in lowered
        or "permission" in lowered
    ):
        return AdbTransportUnavailableError(error.service, detail)
    return AdbTransportUnavailableError(
        error.service,
        detail or "transport unavailable",
    )


__all__ = ["translate_transport_selection_error"]
