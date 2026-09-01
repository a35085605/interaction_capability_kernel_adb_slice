"""Compatibility import for the relocated AOSP server-status decoder."""

from adb.aosp.model.server_status import parse_server_status

__all__ = ["parse_server_status"]
