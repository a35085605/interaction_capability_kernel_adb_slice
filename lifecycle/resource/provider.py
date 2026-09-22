from __future__ import annotations

from typing import Generic, TypeVar

from lifecycle.resource.contract import ResourceProvider, ResourceRequirementsResolver
from lifecycle.resource.driver import (
    RequirementAcquireFailed,
    RequirementAcquireInterrupted,
    RequirementAcquireSucceeded,
    PhysicalResources,
    ResourceDriver,
)
from lifecycle.resource.result import (
    ResourceAcquireFailed,
    ResourceAcquireResult,
    ResourceAcquireSucceeded,
)


RequestT = TypeVar("RequestT")
RequirementT = TypeVar("RequirementT")
PhysicalResourceT = TypeVar("PhysicalResourceT")


class ResolvedResourceProvider(Generic[RequestT, RequirementT, PhysicalResourceT]):
    """Acquire resolved requirements in order and release their physical resources.

    Acquisition stops at the first failure and returns every retained resource reported
    by earlier requirements and by the failing requirement. The provider does not roll
    back resources already transferred by a driver; the lifecycle caller decides when
    to release them.
    """

    def __init__(
        self,
        requirements_resolver: ResourceRequirementsResolver[RequestT, RequirementT],
        driver: ResourceDriver[RequirementT, PhysicalResourceT],
    ) -> None:
        self._requirements_resolver = requirements_resolver
        self._driver = driver

    @property
    def requirements_resolver(self) -> ResourceRequirementsResolver[RequestT, RequirementT]:
        """Return the resolver used to derive requirements for each request."""

        return self._requirements_resolver

    @property
    def driver(self) -> ResourceDriver[RequirementT, PhysicalResourceT]:
        """Return the driver used for physical resource I/O."""

        return self._driver

    def acquire(self, request: RequestT) -> ResourceAcquireResult[PhysicalResourceT]:
        """Acquire all resolved requirements and retain reported resources on failure."""

        if request is None:
            raise TypeError("request cannot be None")

        try:
            requirements = self._requirements_resolver.resolve(request)
        except Exception as exc:
            return ResourceAcquireFailed(exc, ())

        if not isinstance(requirements, tuple):
            return ResourceAcquireFailed(
                TypeError("ResourceRequirementsResolver.resolve() must return a tuple"),
                (),
            )

        resources: PhysicalResources[PhysicalResourceT] = ()
        for requirement in requirements:
            try:
                outcome = self._driver.acquire(requirement)
            except BaseException as exc:
                # Preserve resources acquired by earlier requirements before a
                # control-flow interruption unwinds into the lifecycle coordinator.
                return ResourceAcquireFailed(exc, resources)

            if not isinstance(
                outcome,
                (
                    RequirementAcquireSucceeded,
                    RequirementAcquireFailed,
                    RequirementAcquireInterrupted,
                ),
            ):
                return ResourceAcquireFailed(
                    TypeError("ResourceDriver.acquire() must return a RequirementAcquireResult"),
                    resources,
                )
            if not isinstance(outcome.resources, tuple):
                return ResourceAcquireFailed(
                    TypeError("physical outcome resources must be a PhysicalResources tuple"),
                    resources,
                )

            resources += outcome.resources
            if isinstance(outcome, RequirementAcquireFailed):
                if not isinstance(outcome.error, Exception):
                    return ResourceAcquireFailed(
                        TypeError("RequirementAcquireFailed.error must be an Exception"),
                        resources,
                    )
                return ResourceAcquireFailed(outcome.error, resources)

            if isinstance(outcome, RequirementAcquireInterrupted):
                if not isinstance(outcome.error, BaseException) or isinstance(
                    outcome.error, Exception
                ):
                    return ResourceAcquireFailed(
                        TypeError(
                            "RequirementAcquireInterrupted.error must be a "
                            "non-Exception BaseException"
                        ),
                        resources,
                    )
                return ResourceAcquireFailed(outcome.error, resources)

        return ResourceAcquireSucceeded(resources)

    def release(self, resources: PhysicalResources[PhysicalResourceT]) -> None:
        """Clean up the supplied resources; an empty tuple requires no driver call."""

        if not isinstance(resources, tuple):
            raise TypeError("resources must be a PhysicalResources tuple")
        if resources:
            self._driver.cleanup(resources)


__all__ = ["ResolvedResourceProvider"]
