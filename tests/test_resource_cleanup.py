from __future__ import annotations

import unittest

from lifecycle.resource.cleanup import cleanup_reverse
from lifecycle.resource.result import ResourceCleanupResult, ResourceCleanupStatus


class ResourceCleanupTests(unittest.TestCase):
    def test_reverse_cleanup_stops_before_dependency_after_retryable_failure(self) -> None:
        a, b, c = object(), object(), object()
        first = OSError("c completed with diagnostic")
        second = RuntimeError("b still owned")
        calls: list[object] = []

        def clean(resource: object) -> ResourceCleanupResult[object]:
            calls.append(resource)
            if resource is c:
                return ResourceCleanupResult.complete(errors=(first,))
            if resource is b:
                return ResourceCleanupResult.retryable((b,), errors=(second,))
            self.fail("dependency A must not be cleaned while B remains")

        result = cleanup_reverse((a, b, c), clean)

        self.assertEqual(calls, [c, b])
        self.assertIs(result.status, ResourceCleanupStatus.RETRYABLE)
        self.assertEqual(result.remaining_resources, (a, b))
        self.assertEqual(result.errors, (first, second))

    def test_complete_with_errors_continues_and_aggregates_all_diagnostics(self) -> None:
        a, b = object(), object()
        error_b = OSError("b")
        error_a = OSError("a")

        def clean(resource: object) -> ResourceCleanupResult[object]:
            error = error_a if resource is a else error_b
            return ResourceCleanupResult.complete(errors=(error,))

        result = cleanup_reverse((a, b), clean)

        self.assertIs(result.status, ResourceCleanupStatus.COMPLETE)
        self.assertEqual(result.remaining_resources, ())
        self.assertEqual(result.errors, (error_b, error_a))

    def test_unexpected_cleanup_exception_becomes_blocked(self) -> None:
        resource = object()
        error = RuntimeError("bug")

        def clean(_: object) -> ResourceCleanupResult[object]:
            raise error

        result = cleanup_reverse((resource,), clean)

        self.assertIs(result.status, ResourceCleanupStatus.BLOCKED)
        self.assertEqual(result.remaining_resources, (resource,))
        self.assertEqual(result.errors, (error,))


if __name__ == "__main__":
    unittest.main()
