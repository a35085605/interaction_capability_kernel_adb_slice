from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.server.endpoint import AdbServerEndpoint


@dataclass(frozen=True, slots=True)
class AdbServerPerGenerationEndpoint:
    """Let every newly provisioned server resolve its endpoint independently."""


@dataclass(frozen=True, slots=True)
class AdbServerPinFirstResolvedEndpoint:
    """Pin the first server's resolved endpoint across later provisions."""


@dataclass(frozen=True, slots=True)
class AdbServerFixedEndpoint:
    """Require every provisioned server to use one explicitly configured endpoint."""

    endpoint: AdbServerEndpoint

    def __post_init__(self) -> None:
        if not isinstance(self.endpoint, AdbServerEndpoint):
            raise TypeError("endpoint must be AdbServerEndpoint")


AdbServerEndpointPolicy: TypeAlias = (
    AdbServerPerGenerationEndpoint
    | AdbServerPinFirstResolvedEndpoint
    | AdbServerFixedEndpoint
)


def _require_endpoint_policy(value: object) -> AdbServerEndpointPolicy:
    if not isinstance(
        value,
        (
            AdbServerPerGenerationEndpoint,
            AdbServerPinFirstResolvedEndpoint,
            AdbServerFixedEndpoint,
        ),
    ):
        raise TypeError("endpoint_policy must be an ADB server endpoint policy")
    return value


def resolve_server_provisioning_endpoint(
    endpoint_policy: AdbServerEndpointPolicy,
    first_generation_endpoint: AdbServerEndpoint,
) -> AdbServerEndpoint | None:
    """Resolve the endpoint constraint to inject into a later server provision."""

    policy = _require_endpoint_policy(endpoint_policy)
    if not isinstance(first_generation_endpoint, AdbServerEndpoint):
        raise TypeError("first_generation_endpoint must be AdbServerEndpoint")

    if isinstance(policy, AdbServerPerGenerationEndpoint):
        return None
    if isinstance(policy, AdbServerPinFirstResolvedEndpoint):
        return first_generation_endpoint
    if first_generation_endpoint != policy.endpoint:
        raise ValueError(
            "fixed endpoint policy must match the first generation endpoint"
        )
    return policy.endpoint


__all__ = [
    "AdbServerEndpointPolicy",
    "AdbServerFixedEndpoint",
    "AdbServerPerGenerationEndpoint",
    "AdbServerPinFirstResolvedEndpoint",
    "resolve_server_provisioning_endpoint",
]
