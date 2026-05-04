"""Fork-only middleware overlays.

Lives in fork-owned `ee/` namespace so it survives upstream syncs without
touching `posthog/views.py` or `posthog/utils.py`. Wired in via `ee/settings.py`
appending entries to MIDDLEWARE — settings overlay is loaded LAST in
`posthog/settings/__init__.py`, so we get the final say.
"""

import json
import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)


class PreflightKafkaProbeMiddleware(MiddlewareMixin):
    """Patch /_preflight responses to actually probe Kafka.

    Upstream `posthog.views.preflight_check` returns:

        "kafka": is_cloud() or settings.TEST

    On a self-hosted, non-test instance this is always False, so the
    onboarding "Validate implementation" wizard shows "Queue · Kafka: Error"
    even on a fully healthy stack. Self-hosted FOSS deploys live or die by
    Kafka, so silently broken is worse than a slightly-too-optimistic OK.

    This middleware intercepts only `/_preflight` responses, runs a single
    `AdminClient.list_topics(timeout=2)` metadata request against the
    configured brokers, and rewrites the kafka field to the real result.
    All other endpoints pass through untouched. Probe failures collapse to
    False (preserving upstream behavior).

    Place this AFTER any auth middleware but BEFORE compression — preflight
    is unauthenticated and the rewrite needs to happen on the raw JSON
    bytes before gzip seals them.
    """

    PREFLIGHT_PATH = "/_preflight"

    def process_response(self, request: HttpRequest, response: HttpResponse) -> HttpResponse:
        if request.path.rstrip("/") != self.PREFLIGHT_PATH:
            return response
        # JsonResponse always sets this; bail out on edge cases (errors,
        # redirects, custom content types) without touching the body.
        if response.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return response

        try:
            data = json.loads(response.content)
        except (ValueError, AttributeError):
            return response

        if data.get("kafka") or settings.TEST:
            return response

        data["kafka"] = self._probe_kafka()
        new_body = json.dumps(data).encode("utf-8")
        response.content = new_body
        # JsonResponse caches Content-Length; rewrite to match new body or
        # downstream gzip/whitenoise will mis-frame the response.
        response["Content-Length"] = str(len(new_body))
        return response

    @staticmethod
    def _probe_kafka() -> bool:
        try:
            from confluent_kafka.admin import AdminClient

            admin = AdminClient({"bootstrap.servers": ",".join(settings.KAFKA_HOSTS)})
            metadata = admin.list_topics(timeout=2.0)
            return len(metadata.brokers) > 0
        except BaseException as exc:  # noqa: BLE001 — preflight must never raise
            logger.debug("kafka_preflight_probe_failed", exc_info=exc)
            return False
