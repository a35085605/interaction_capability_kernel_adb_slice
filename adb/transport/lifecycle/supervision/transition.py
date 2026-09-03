from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from adb.transport.configuration import (
    AdbConfiguredTransport,
    AdbTcpTransportConfiguration,
    AdbUsbTransportConfiguration,
)
from adb.transport.lifecycle.ensure import (
    AdbTcpTransportEnsureResult,
    AdbTcpTransportEnsureStatus,
)
from adb.transport.lifecycle.supervision.policy import AdbConfiguredTransportSupervisionPolicy
from adb.transport.lifecycle.supervision.signal import AdbConfiguredTransportRecoveryExhausted
from adb.transport.resolution import (
    AdbConfiguredTransportProjection,
    AdbConfiguredTransportResolutionStatus,
)


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportRecoveryIdle:
    """Instruction that no recovery-side effect is required for the current evidence."""


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportStartRecovery:
    """Instruction to start one bounded recovery episode for a configured transport."""

    configuration: AdbConfiguredTransport

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, AdbConfiguredTransport):
            raise TypeError("configuration must be AdbConfiguredTransport")


@dataclass(frozen=True, slots=True)
class AdbConfiguredTransportPublishRecoveryExhausted:
    """Instruction to publish terminal unsatisfied recovery evidence."""

    signal: AdbConfiguredTransportRecoveryExhausted

    def __post_init__(self) -> None:
        if not isinstance(self.signal, AdbConfiguredTransportRecoveryExhausted):
            raise TypeError("signal must be AdbConfiguredTransportRecoveryExhausted")


AdbConfiguredTransportRecoveryInstruction: TypeAlias = (
    AdbConfiguredTransportRecoveryIdle
    | AdbConfiguredTransportStartRecovery
    | AdbConfiguredTransportPublishRecoveryExhausted
)


def decide_recovery_after_projection(
    configuration: AdbConfiguredTransport,
    policy: AdbConfiguredTransportSupervisionPolicy,
    projection: AdbConfiguredTransportProjection,
    *,
    recovery_active: bool,
) -> AdbConfiguredTransportRecoveryInstruction:
    """Reduce one validated configured-transport projection to a recovery instruction."""

    if not isinstance(configuration, AdbConfiguredTransport):
        raise TypeError("configuration must be AdbConfiguredTransport")
    if not isinstance(policy, AdbConfiguredTransportSupervisionPolicy):
        raise TypeError("policy must be AdbConfiguredTransportSupervisionPolicy")
    if not isinstance(projection, AdbConfiguredTransportProjection):
        raise TypeError("projection must be AdbConfiguredTransportProjection")
    if projection.configuration != configuration:
        raise ValueError("projection configuration must match configured transport")
    if not isinstance(recovery_active, bool):
        raise TypeError("recovery_active must be bool")

    match configuration.transport:
        case AdbUsbTransportConfiguration():
            if policy.tcp_recovery_ensure_policy is not None:
                raise ValueError("USB configured transports cannot enable TCP recovery")
            return AdbConfiguredTransportRecoveryIdle()
        case AdbTcpTransportConfiguration():
            if (
                projection.status is AdbConfiguredTransportResolutionStatus.ABSENT
                and policy.tcp_recovery_ensure_policy is not None
                and not recovery_active
            ):
                return AdbConfiguredTransportStartRecovery(configuration)
            return AdbConfiguredTransportRecoveryIdle()
        case _:
            raise TypeError("unsupported configured transport type")


def decide_recovery_after_ensure(
    configuration: AdbConfiguredTransport,
    result: AdbTcpTransportEnsureResult,
) -> AdbConfiguredTransportRecoveryInstruction:
    """Reduce one terminal ensure result to the configured-transport recovery side effect."""

    if not isinstance(configuration, AdbConfiguredTransport):
        raise TypeError("configuration must be AdbConfiguredTransport")
    if not isinstance(result, AdbTcpTransportEnsureResult):
        raise TypeError("result must be AdbTcpTransportEnsureResult")
    if result.operation.configuration != configuration:
        raise ValueError("ensure result configuration does not match supervised transport")

    if result.status is AdbTcpTransportEnsureStatus.SATISFIED:
        return AdbConfiguredTransportRecoveryIdle()
    return AdbConfiguredTransportPublishRecoveryExhausted(
        AdbConfiguredTransportRecoveryExhausted(configuration, result)
    )


__all__ = [
    "AdbConfiguredTransportPublishRecoveryExhausted",
    "AdbConfiguredTransportRecoveryIdle",
    "AdbConfiguredTransportRecoveryInstruction",
    "AdbConfiguredTransportStartRecovery",
    "decide_recovery_after_ensure",
    "decide_recovery_after_projection",
]
