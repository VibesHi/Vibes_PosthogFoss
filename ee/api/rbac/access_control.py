"""FOSS stub for ee.api.rbac.access_control.AccessControlViewSetMixin.

Used as a base class for ProjectViewSet, TeamViewSet, and ~25 product
viewsets (NotebookViewSet, SurveyViewSet, ExternalDataSourceViewSet, etc.).
Inheriting from a no-op mixin is the cleanest way to satisfy the import
without rewriting the ViewSet class signatures upstream.

CRITICAL: this is the class that's *actually loaded* on FOSS. The fallback
class in `posthog/rbac/access_control_api_mixin.py` only triggers if THIS
module fails to import -- which it doesn't, because we ship this stub.

Methods needed for the MRO not to break:
- `dangerously_get_required_scopes(request, view) -> list[str] | None`:
  posthog/api/team.py:1354 and posthog/api/project.py:690 call
  `super().dangerously_get_required_scopes(...)` UNCONDITIONALLY. If this
  method is absent, super() walks past the mixin, never finds a parent
  with the method, raises AttributeError. Triggered on every PATCH to
  /api/environments/<id>/ and /api/projects/<id>/. Returning `None`
  signals "no EE-specific scope override" -- which is how the real
  upstream class behaves whenever the request action isn't one of the
  EE-only access_control endpoints.

The 7 @action-decorated endpoints upstream provides (`access_controls`,
`resource_access_controls`, `global_access_controls`, `users_with_access`,
`access_control_defaults`, `access_control_roles`, `access_control_members`)
are intentionally NOT stubbed: missing @action methods just don't generate
URLs, so the frontend gets a 404 (not a 500) if it ever calls them. The
frontend gates these UI surfaces behind ADVANCED_PERMISSIONS feature
checks anyway.
"""


class AccessControlViewSetMixin:
    """No-op mixin. Upstream version checks per-resource ACLs; FOSS allows all."""

    def dangerously_get_required_scopes(self, request, view) -> list[str] | None:
        return None
