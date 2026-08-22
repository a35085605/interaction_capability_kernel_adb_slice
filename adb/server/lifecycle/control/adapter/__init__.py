"""Concrete adapters for native ADB server lifecycle control."""

from adb.server.lifecycle.control.adapter.subprocess import SubprocessAdbServerController

__all__ = ["SubprocessAdbServerController"]
