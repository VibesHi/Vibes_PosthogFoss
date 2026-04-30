"""FOSS stub. Imported lazily inside `if EE_AVAILABLE:` branches in
posthog/taxonomy/property_definition_api.py.
"""

from rest_framework import serializers


class EnterprisePropertyDefinitionSerializer(serializers.Serializer):
    pass
