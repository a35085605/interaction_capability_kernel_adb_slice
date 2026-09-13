from __future__ import annotations

from dataclasses import dataclass, field
import unittest

from _lifecycle_new.resource.driver import (
    RequirementAcquireFailed,
    RequirementAcquireSucceeded,
)
from _lifecycle_new.resource.manager import ResolvedResourceProvider
from _lifecycle_new.resource.result import ResourceAcquireFailed, ResourceAcquireSucceeded


class _Abort(BaseException):
    pass


@dataclass
class _Resolver:
    outcome: object
    calls: list[object] = field(default_factory=list, init=False)

    def resolve(self, request):
        self.calls.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@dataclass
class _Driver:
    outcomes: list[object]
    acquire_calls: list[object] = field(default_factory=list, init=False)
    cleanup_calls: list[tuple[object, ...]] = field(default_factory=list, init=False)
    cleanup_error: BaseException | None = None

    def acquire(self, requirement):
        self.acquire_calls.append(requirement)
        if not self.outcomes:
            raise AssertionError("unexpected driver acquire call")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def cleanup(self, resources):
        self.cleanup_calls.append(resources)
        if self.cleanup_error is not None:
            raise self.cleanup_error


class ResolvedResourceProviderTests(unittest.TestCase):
    def test_acquire_aggregates_resources_in_requirement_order(self) -> None:
        resolver = _Resolver(("first", "second"))
        driver = _Driver(
            [
                RequirementAcquireSucceeded(("r1",)),
                RequirementAcquireSucceeded(("r2", "r3")),
            ]
        )
        provider = ResolvedResourceProvider(resolver, driver)

        result = provider.acquire("request")

        self.assertIsInstance(result, ResourceAcquireSucceeded)
        self.assertEqual(result.resources, ("r1", "r2", "r3"))
        self.assertEqual(driver.acquire_calls, ["first", "second"])

    def test_terminal_requirement_failure_retains_all_reported_resources(self) -> None:
        error = RuntimeError("second failed")
        driver = _Driver(
            [
                RequirementAcquireSucceeded(("r1",)),
                RequirementAcquireFailed(error, ("r2-partial",)),
            ]
        )
        provider = ResolvedResourceProvider(_Resolver(("first", "second")), driver)

        result = provider.acquire("request")

        self.assertIsInstance(result, ResourceAcquireFailed)
        self.assertIs(result.error, error)
        self.assertEqual(result.resources, ("r1", "r2-partial"))

    def test_driver_exception_retains_resources_from_prior_requirements(self) -> None:
        error = OSError("driver failed")
        driver = _Driver(
            [
                RequirementAcquireSucceeded(("r1",)),
                error,
            ]
        )
        provider = ResolvedResourceProvider(_Resolver(("first", "second")), driver)

        result = provider.acquire("request")

        self.assertIsInstance(result, ResourceAcquireFailed)
        self.assertIs(result.error, error)
        self.assertEqual(result.resources, ("r1",))

    def test_resolver_base_exception_is_returned_as_failure(self) -> None:
        error = _Abort("resolver interrupted")
        provider = ResolvedResourceProvider(_Resolver(error), _Driver([]))

        result = provider.acquire("request")

        self.assertIsInstance(result, ResourceAcquireFailed)
        self.assertIs(result.error, error)
        self.assertEqual(result.resources, ())

    def test_invalid_resolver_or_driver_results_become_failures(self) -> None:
        invalid_requirements = ResolvedResourceProvider(_Resolver(["not", "tuple"]), _Driver([]))
        result = invalid_requirements.acquire("request")
        self.assertIsInstance(result, ResourceAcquireFailed)
        self.assertIsInstance(result.error, TypeError)
        self.assertEqual(result.resources, ())

        driver = _Driver(
            [
                RequirementAcquireSucceeded(("r1",)),
                object(),
            ]
        )
        invalid_outcome = ResolvedResourceProvider(_Resolver(("first", "second")), driver)
        result = invalid_outcome.acquire("request")
        self.assertIsInstance(result, ResourceAcquireFailed)
        self.assertIsInstance(result.error, TypeError)
        self.assertEqual(result.resources, ("r1",))

    def test_release_skips_empty_resources_and_propagates_cleanup_failure(self) -> None:
        cleanup_error = OSError("cleanup failed")
        driver = _Driver([], cleanup_error=cleanup_error)
        provider = ResolvedResourceProvider(_Resolver(()), driver)

        provider.release(())
        self.assertEqual(driver.cleanup_calls, [])

        with self.assertRaises(OSError) as raised:
            provider.release(("r1",))

        self.assertIs(raised.exception, cleanup_error)
        self.assertEqual(driver.cleanup_calls, [("r1",)])


if __name__ == "__main__":
    unittest.main()
