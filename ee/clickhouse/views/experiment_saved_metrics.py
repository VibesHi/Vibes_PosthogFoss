"""FOSS stub for ee.clickhouse.views.experiment_saved_metrics.

Imported by products/experiments/backend/experiment_service.py. The full
experiments product still works without saved metrics -- it's an EE feature
to share metric definitions across experiments.
"""

from rest_framework import serializers


class ExperimentToSavedMetricSerializer(serializers.Serializer):
    """No-op serializer. Never instantiated on FOSS."""

    pass


class ExperimentSavedMetricViewSet:
    """No-op viewset placeholder. Not registered in the router on FOSS."""

    pass
