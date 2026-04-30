"""FOSS stub. Imported only inside `if EE_AVAILABLE:` (False) in posthog/api/__init__.py.

Note: basic group analytics still work via the OSS GroupsViewSet in posthog/api/group.py;
this EE viewset is the enterprise variant with extra usage-metrics fields."""

from rest_framework import viewsets


class GroupsViewSet(viewsets.ViewSet):
    pass


class GroupsTypesViewSet(viewsets.ViewSet):
    pass


class GroupUsageMetricViewSet(viewsets.ViewSet):
    pass
