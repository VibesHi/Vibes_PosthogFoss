"""FOSS stub. Imported only inside `if EE_AVAILABLE:` (False) in posthog/api/__init__.py."""

from rest_framework import viewsets


class EnterprisePersonViewSet(viewsets.ViewSet):
    pass


class LegacyEnterprisePersonViewSet(viewsets.ViewSet):
    pass
