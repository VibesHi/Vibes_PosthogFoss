"""FOSS stub for ee.api.ee_event_definition.EnterpriseEventDefinitionSerializer.

Imported lazily inside `if EE_AVAILABLE:` branches in posthog/api/event_definition.py.
With EE_AVAILABLE=False those branches don't run, but defensive stub anyway.
"""

from rest_framework import serializers


class EnterpriseEventDefinitionSerializer(serializers.Serializer):
    """No-op serializer. Never instantiated on a FOSS deploy."""

    pass
