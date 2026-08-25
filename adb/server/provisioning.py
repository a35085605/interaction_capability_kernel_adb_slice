from __future__ import annotations

from threading import Lock
from typing import Protocol, runtime_checkable

from adb.server.endpoint import AdbServerEndpoint


@runtime_checkable
class AdbServerProvisioningView(Protocol):
    """Read-only runtime-scoped server provisioning constraint."""

    @property
    def required_endpoint(self) -> AdbServerEndpoint | None: ...


class AdbServerProvisioningState(AdbServerProvisioningView):
    """Persist the endpoint constraint applied to each fresh server lifetime."""

    def __init__(self, required_endpoint: AdbServerEndpoint | None = None) -> None:
        if required_endpoint is not None and not isinstance(
            required_endpoint, AdbServerEndpoint
        ):
            raise TypeError("required_endpoint must be AdbServerEndpoint or None")
        self._lock = Lock()
        self._required_endpoint = required_endpoint

    @property
    def required_endpoint(self) -> AdbServerEndpoint | None:
        with self._lock:
            return self._required_endpoint

    def set_required_endpoint(
        self,
        endpoint: AdbServerEndpoint | None,
    ) -> None:
        """Replace the constraint used by subsequent server provisioning attempts."""

        if endpoint is not None and not isinstance(endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint or None")
        with self._lock:
            self._required_endpoint = endpoint


__all__ = ["AdbServerProvisioningState", "AdbServerProvisioningView"]
