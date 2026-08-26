from __future__ import annotations

import unittest

from adb.server.endpoint import AdbServerEndpoint
from adb.server.lifecycle.control.backend import (
    AdbServerBackendPhase,
    AdbServerBackendRequest,
    abort_backend_acquire,
    begin_backend_request,
    complete_backend_request,
    fail_backend_release_unproven,
    require_backend_release_endpoint,
)
from adb.server.lifecycle.control.errors import (
    AdbServerAcquireInProgressError,
    AdbServerAttachmentMismatchError,
    AdbServerNativeLifetimeBusyError,
    AdbServerNativeTerminationUnprovenError,
    AdbServerNoAttachmentError,
    AdbServerStopInProgressError,
)


class AdbServerBackendContractTests(unittest.TestCase):
    def test_acquire_admission_matrix(self) -> None:
        self.assertIs(
            begin_backend_request(
                AdbServerBackendPhase.IDLE,
                AdbServerBackendRequest.ACQUIRE,
            ),
            AdbServerBackendPhase.ACQUIRING,
        )

        for phase in (AdbServerBackendPhase.ACQUIRING, AdbServerBackendPhase.ACTIVE):
            with self.subTest(phase=phase):
                with self.assertRaises(AdbServerNativeLifetimeBusyError):
                    begin_backend_request(phase, AdbServerBackendRequest.ACQUIRE)

        with self.assertRaises(AdbServerStopInProgressError):
            begin_backend_request(
                AdbServerBackendPhase.RELEASING,
                AdbServerBackendRequest.ACQUIRE,
            )
        with self.assertRaises(AdbServerNativeTerminationUnprovenError):
            begin_backend_request(
                AdbServerBackendPhase.INDETERMINATE,
                AdbServerBackendRequest.ACQUIRE,
            )

    def test_release_admission_matrix(self) -> None:
        self.assertIs(
            begin_backend_request(
                AdbServerBackendPhase.ACTIVE,
                AdbServerBackendRequest.RELEASE,
            ),
            AdbServerBackendPhase.RELEASING,
        )

        cases = (
            (AdbServerBackendPhase.IDLE, AdbServerNoAttachmentError),
            (AdbServerBackendPhase.ACQUIRING, AdbServerAcquireInProgressError),
            (AdbServerBackendPhase.RELEASING, AdbServerStopInProgressError),
            (
                AdbServerBackendPhase.INDETERMINATE,
                AdbServerNativeTerminationUnprovenError,
            ),
        )
        for phase, error_type in cases:
            with self.subTest(phase=phase):
                with self.assertRaises(error_type):
                    begin_backend_request(phase, AdbServerBackendRequest.RELEASE)

    def test_successful_completion_matrix(self) -> None:
        self.assertIs(
            complete_backend_request(
                AdbServerBackendPhase.ACQUIRING,
                AdbServerBackendRequest.ACQUIRE,
            ),
            AdbServerBackendPhase.ACTIVE,
        )
        self.assertIs(
            complete_backend_request(
                AdbServerBackendPhase.RELEASING,
                AdbServerBackendRequest.RELEASE,
            ),
            AdbServerBackendPhase.IDLE,
        )

    def test_failed_acquire_has_port_defined_cleanup_states(self) -> None:
        self.assertIs(
            abort_backend_acquire(
                AdbServerBackendPhase.ACQUIRING,
                native_termination_proven=True,
            ),
            AdbServerBackendPhase.IDLE,
        )
        self.assertIs(
            abort_backend_acquire(
                AdbServerBackendPhase.ACQUIRING,
                native_termination_proven=False,
            ),
            AdbServerBackendPhase.INDETERMINATE,
        )

    def test_unproven_release_enters_indeterminate(self) -> None:
        self.assertIs(
            fail_backend_release_unproven(AdbServerBackendPhase.RELEASING),
            AdbServerBackendPhase.INDETERMINATE,
        )

    def test_release_endpoint_identity_uses_domain_failure(self) -> None:
        owned = AdbServerEndpoint("127.0.0.1", 5037)
        requested = AdbServerEndpoint("127.0.0.1", 5038)
        with self.assertRaises(AdbServerAttachmentMismatchError):
            require_backend_release_endpoint(owned, requested)


if __name__ == "__main__":
    unittest.main()
