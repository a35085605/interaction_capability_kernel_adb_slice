"""Concrete ADB server lifecycle control adapters."""

from adb.server.lifecycle.control.adapter.subprocess import SubprocessAdbServerController

__all__ = ["SubprocessAdbServerController"]
