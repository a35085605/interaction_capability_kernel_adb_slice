"""Compatibility transport lifecycle namespace.

Explicit connect/disconnect commands now live in ``adb.transport.control``.
"""

from adb.transport.control import *  # noqa: F403
from adb.transport.control import __all__
