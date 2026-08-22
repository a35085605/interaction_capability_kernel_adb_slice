from __future__ import annotations

from collections.abc import Callable
from threading import Lock

from adb.server.lifecycle.control.port import (
    AdbServerStart,
    AdbServerStartError,
    AdbServerStop,
    AdbServerStopError,
)
from adb.server.endpoint import AdbServerEndpoint
from adb.server.identity import AdbServer, _AdbServerSequence
from adb.server.lifecycle.handle import AdbServerProcessLifetime
from adb.server.lifecycle.launch import AdbServerLauncher
from adb.server.lifecycle.subprocess import SubprocessAdbServerLauncher


_ServerFactory = Callable[[AdbServerEndpoint], AdbServer]


class SubprocessAdbServerController:
    """Start and stop exact subprocess-backed ADB server lifetimes.

    The low-level launcher is an implementation detail of :meth:`start`.  Its returned exact
    process lifetime is retained privately and fenced by the minted :class:`AdbServer` identity,
    so :meth:`stop` cannot accidentally target a newer generation that reused the same endpoint.
    """

    def __init__(
        self,
        *,
        executable: str = "adb",
        startup_timeout_seconds: float = 5.0,
        shutdown_timeout_seconds: float = 5.0,
        probe_interval_seconds: float = 0.05,
        _server_factory: _ServerFactory | None = None,
        _launcher: AdbServerLauncher | None = None,
    ) -> None:
        if _server_factory is None:
            _server_factory = _AdbServerSequence().next
        if not callable(_server_factory):
            raise TypeError("_server_factory must be callable")
        if _launcher is None:
            _launcher = SubprocessAdbServerLauncher(
                executable=executable,
                startup_timeout_seconds=startup_timeout_seconds,
                shutdown_timeout_seconds=shutdown_timeout_seconds,
                probe_interval_seconds=probe_interval_seconds,
            )
        elif not isinstance(_launcher, AdbServerLauncher):
            raise TypeError("_launcher must satisfy AdbServerLauncher")

        self._server_factory = _server_factory
        self._launcher = _launcher
        self._lock = Lock()
        self._process_lifetimes: dict[AdbServer, AdbServerProcessLifetime] = {}

    def start(
        self,
        endpoint: AdbServerEndpoint | None = None,
    ) -> AdbServerStart:
        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")

        process_lifetime = self._launcher.launch(endpoint)
        if not isinstance(process_lifetime, AdbServerProcessLifetime):
            raise TypeError("launcher.launch() must return AdbServerProcessLifetime")

        try:
            server = self._server_factory(process_lifetime.endpoint)
            if not isinstance(server, AdbServer):
                raise TypeError("_server_factory must return AdbServer")
            if server.endpoint != process_lifetime.endpoint:
                raise ValueError("server endpoint must match started process endpoint")
            with self._lock:
                if server in self._process_lifetimes:
                    raise RuntimeError("server identity is already bound to a process lifetime")
                self._process_lifetimes[server] = process_lifetime
            return AdbServerStart(server)
        except BaseException:
            try:
                process_lifetime.close()
            except BaseException as stop_error:
                raise AdbServerStartError(
                    "ADB server start failed and its child process could not be stopped"
                ) from stop_error
            raise

    def stop(self, server: AdbServer) -> AdbServerStop:
        if not isinstance(server, AdbServer):
            raise TypeError("server must be AdbServer")

        with self._lock:
            process_lifetime = self._process_lifetimes.get(server)
        if process_lifetime is None:
            raise AdbServerStopError(
                "no exact process lifetime is registered for the requested ADB server"
            )

        process_lifetime.close()

        with self._lock:
            current = self._process_lifetimes.get(server)
            if current is process_lifetime:
                del self._process_lifetimes[server]
        return AdbServerStop(server)


__all__ = ["SubprocessAdbServerController"]
