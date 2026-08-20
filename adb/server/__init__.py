"""ADB server acquisition, process ownership, endpoint, and status ownership."""

from adb.server.acquisition import (
    AdbServerAcquisitionError,
    AdbServerAcquisitionPolicy,
    AdbServerCandidateAttempt,
    AdbServerCandidateOutcome,
)
from adb.server.endpoint import (
    AdbServerEndpoint,
    AdbServerEndpointObserver,
    EndpointObservation,
    EndpointObservationStatus,
)
from adb.server.ownership import (
    AdbOwnedServer,
    AdbServerConfigurationConflictError,
    AdbServerOwnershipLostError,
    AdbServerRef,
    ProcessAdbServerSlot,
    acquire_process_adb_server,
)
from adb.server.status import AdbMdnsBackend, AdbServerStatus, AdbServerStatusReader, AdbUsbBackend

__all__ = [
    "AdbMdnsBackend",
    "AdbOwnedServer",
    "AdbServerAcquisitionError",
    "AdbServerAcquisitionPolicy",
    "AdbServerCandidateAttempt",
    "AdbServerCandidateOutcome",
    "AdbServerConfigurationConflictError",
    "AdbServerEndpoint",
    "AdbServerEndpointObserver",
    "AdbServerOwnershipLostError",
    "AdbServerRef",
    "AdbServerStatus",
    "AdbServerStatusReader",
    "AdbUsbBackend",
    "EndpointObservation",
    "EndpointObservationStatus",
    "ProcessAdbServerSlot",
    "acquire_process_adb_server",
]
