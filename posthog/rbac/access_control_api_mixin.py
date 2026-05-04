from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ee.api.rbac.access_control import AccessControlViewSetMixin
else:
    try:
        from ee.api.rbac.access_control import AccessControlViewSetMixin

    except ImportError:
        # Multiple OSS viewsets call `super().dangerously_get_required_scopes(...)`
        # expecting this mixin to provide it (e.g. posthog/api/team.py:1354,
        # posthog/api/project.py:690). When the EE class is missing AND the stub
        # has no method, super() walks past the mixin and lands on a class that
        # doesn't define it -> AttributeError 500 on every PATCH /api/environments/<id>/
        # and /api/projects/<id>/ that goes through these viewsets.
        #
        # Returning None signals "no EE-specific scope override" -- callers fall
        # through to their default scope-resolution logic, which is exactly what
        # the upstream EE mixin does when the request doesn't match an
        # access-controlled resource.
        class AccessControlViewSetMixin:
            def dangerously_get_required_scopes(self, request, view) -> list[str] | None:
                return None
