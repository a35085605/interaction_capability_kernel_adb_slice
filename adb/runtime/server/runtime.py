from __future__ import annotations

from dataclasses import dataclass

from adb.runtime.server.commands import AdbServerCommands
from adb.server.snapshot import AdbServerSnapshot, AdbServerSnapshotReader


class _AdbServerRuntimeSnapshotReader:
    """Expose the server lifecycle read side without exposing lifecycle commands."""

    __slots__ = ("_reader",)

    def __init__(self, reader: AdbServerSnapshotReader) -> None:
        if not isinstance(reader, AdbServerSnapshotReader):
            raise TypeError("reader must satisfy AdbServerSnapshotReader")
        self._reader = reader

    def read(self) -> AdbServerSnapshot:
        return self._reader.read()


@dataclass(frozen=True, slots=True)
class AdbServerRuntime:
    """Public Runtime surface for one ADB server ownership scope."""

    snapshot: AdbServerSnapshotReader
    commands: AdbServerCommands

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, AdbServerSnapshotReader):
            raise TypeError("snapshot must satisfy AdbServerSnapshotReader")
        if not isinstance(self.commands, AdbServerCommands):
            raise TypeError("commands must be AdbServerCommands")


def _snapshot_reader(reader: AdbServerSnapshotReader) -> AdbServerSnapshotReader:
    return _AdbServerRuntimeSnapshotReader(reader)


__all__ = ["AdbServerRuntime"]
