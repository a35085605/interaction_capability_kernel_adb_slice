"""Compatibility import for the resource provider implementation.

New code should import ``ResolvedResourceProvider`` from ``lifecycle.resource.provider``.
"""

from lifecycle.resource.provider import (
    ResolvedResourceProvider,
    ResourceProvider,
    ResourceRequirementsResolver,
)


__all__ = ["ResolvedResourceProvider", "ResourceProvider", "ResourceRequirementsResolver"]
