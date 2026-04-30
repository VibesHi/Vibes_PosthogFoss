"""FOSS stub. Imported only inside `if EE_AVAILABLE:` (False) in posthog/api/__init__.py.

The OSS branch (posthog/api/__init__.py:852+) registers the regular InsightViewSet
instead, so end-users get unfettered insight access."""

from rest_framework import viewsets


class EnterpriseInsightsViewSet(viewsets.ViewSet):
    pass
