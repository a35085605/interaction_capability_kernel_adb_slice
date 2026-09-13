"""Compatibility alias for the pre-migration watch request name."""

from adb.transport_list.watch.request import AdbTransportListWatchRequest


AdbTransportListWatchAccess = AdbTransportListWatchRequest


__all__ = ["AdbTransportListWatchAccess"]
