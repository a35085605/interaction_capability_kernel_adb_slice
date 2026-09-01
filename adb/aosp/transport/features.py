"""Compatibility imports for relocated AOSP transport-feature translation."""

from adb.adapters.aosp.transport_features import parse_transport_features
from adb.transport.features import AdbTransportFeatures

__all__ = ["AdbTransportFeatures", "parse_transport_features"]
