"""FOSS stub for ee.hogai.session_summaries.session.output_data."""

from rest_framework import serializers


class SessionSummarySerializer(serializers.Serializer):
    """No-op DRF serializer. Never instantiated on FOSS."""

    pass


class OutcomeSerializer(serializers.Serializer):
    """Used by posthog/session_recordings/session_recording_api.py to validate
    the LLM-generated session summary outcome payload before returning it."""

    pass
