from __future__ import annotations

from collections.abc import Callable
from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer
from adb.server.lifecycle.handle import AdbServerCloseError, AdbServerProcessLifetime
from adb.server.lifecycle.launch import AdbServerLauncher


_ServerFactory = Callable[[AdbServerEndpoint], AdbServer]


@runtime_checkable
class AdbServerLifecycleBackend(Protocol):
    """Backend boundary for fresh ADB server creation and exact-lifetime teardown.

    Implementations own any OS/process handles internally.  The ADB ownership layer receives
    only :class:`AdbServer` identities and therefore never needs to retain ``Popen`` objects,
    PIDs, native handles, or other process-ownership details.
    """

    def create(
        self,
        endpoint: AdbServerEndpoint | None,
        *,
        server_factory: _ServerFactory,
    ) -> AdbServer:
        """Create one fresh server and return its ADB identity."""
        ...

    def close(self, server: AdbServer) -> None:
        """Close the exact backend lifetime associated with ``server`` when available."""
        ...


class LauncherAdbServerLifecycleBackend:
    """Keep process-lifetime capabilities private behind an ADB lifecycle backend.

    ``AdbServerLauncher`` remains useful as the low-level process creation primitive, but its
    returned lifetime object is captured here and never stored by ``adb.server.ownership``.
    """

    def __init__(self, launcher: AdbServerLauncher) -> None:
        if not isinstance(launcher, AdbServerLauncher):
            raise TypeError("launcher must satisfy AdbServerLauncher")
        self._launcher = launcher
        self._lock = Lock()
        self._process_lifetimes: dict[AdbServer, AdbServerProcessLifetime] = {}

    def create(
        self,
        endpoint: AdbServerEndpoint | None,
        *,
        server_factory: _ServerFactory,
    ) -> AdbServer:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")
        if not callable(server_factory):
            raise TypeError("server_factory must be callable")

        process_lifetime = self._launcher.launch(endpoint)
        if not isinstance(process_lifetime, AdbServerProcessLifetime):
            raise TypeError("launcher.launch() must return AdbServerProcessLifetime")

        try:
            server = server_factory(process_lifetime.endpoint)
            if not isinstance(server, AdbServer):
                raise TypeError("server_factory must return AdbServer")
            if server.endpoint != process_lifetime.endpoint:
                raise ValueError("server endpoint must match launched process endpoint")
            with self._lock:
                if server in self._process_lifetimes:
                    raise RuntimeError("server identity is already bound to a process lifetime")
                self._process_lifetimes[server] = process_lifetime
            return server
        except BaseException as create_error:
            try:
                process_lifetime.close()
            except BaseException as close_error:
                raise close_error from create_error
            raise

    def close(self, server: AdbServer) -> None:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")
        with self._lock:
            process_lifetime = self._process_lifetimes.get(server)
        if process_lifetime is None:
            raise AdbServerCloseError(
                "no exact process lifetime is registered for the requested ADB server"
            )

        process_lifetime.close()

        with self._lock:
            current = self._process_lifetimes.get(server)
            if current is process_lifetime:
                del self._process_lifetimes[server]


__all__ = ["AdbServerLifecycleBackend", "LauncherAdbServerLifecycleBackend"]
