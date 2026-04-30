"""FOSS stub for ee.api.rbac.access_control.AccessControlViewSetMixin.

Used as a base class for ProjectViewSet and others. Inheriting from a no-op
mixin is the cleanest way to satisfy the import without rewriting the
ViewSet class signatures.
"""


class AccessControlViewSetMixin:
    """No-op mixin. Upstream version checks per-resource ACLs; FOSS allows all."""
