"""FOSS stub: Vercel marketplace installation viewset (EE feature)."""

from rest_framework import status, viewsets
from rest_framework.response import Response


class VercelInstallationViewSet(viewsets.ViewSet):
    def list(self, request, *args, **kwargs):
        return Response(
            {"detail": "Vercel integration unavailable in this FOSS deployment."},
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )
