"""FOSS stub for ee.api.session_summaries.

posthog/api/__init__.py:115 imports SessionGroupSummaryViewSet at module
top level (unconditional) and registers it on the projects_router at line
918 -- also unconditional. So this stub MUST be a real DRF ViewSet that
the router can register without error.

The route gets created at /api/projects/{project_id}/session_group_summaries/
and returns 501 Not Implemented for any verb. EE_AVAILABLE=False already
disables the AI summary frontend, so the route is reachable in theory but
not exercised in practice.
"""

from rest_framework import status, viewsets
from rest_framework.response import Response


class SessionGroupSummaryViewSet(viewsets.ViewSet):
    def list(self, request, *args, **kwargs):
        return Response(
            {"detail": "AI session summaries are unavailable in this FOSS deployment."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    def retrieve(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)
