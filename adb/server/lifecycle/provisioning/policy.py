from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.endpoint import AdbServerEndpoint


@dataclass(frozen=True, slots=True)
class AdbServerPerGenerationEndpoint:
    """Let every newly provisioned server resolve its endpoint independently."""


@dataclass(frozen=True, slots=True)
class AdbServerFixedEndpoint:
    """Require every newly provisioned server to use one fixed endpoint."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


AdbServerEndpointPolicy: TypeAlias = (
    AdbServerPerGenerationEndpoint | AdbServerFixedEndpoint
)


def _require_endpoint_policy(value: object) -> AdbServerEndpointPolicy:
    if not isinstance(
        value,
        (
            AdbServerPerGenerationEndpoint,
            AdbServerFixedEndpoint,
        ),
    ):
        raise TypeError("endpoint_policy must be an ADB server endpoint policy")
    return value


def resolve_server_provisioning_endpoint(
    endpoint_policy: AdbServerEndpointPolicy,
) -> AdbServerEndpoint | None:
    """Resolve the endpoint constraint to pass to one server provision."""

    policy = _require_endpoint_policy(endpoint_policy)
    if isinstance(policy, AdbServerPerGenerationEndpoint):
        return None
    return policy.endpoint


__all__ = [
    "AdbServerEndpointPolicy",
    "AdbServerFixedEndpoint",
    "AdbServerPerGenerationEndpoint",
    "resolve_server_provisioning_endpoint",
]
