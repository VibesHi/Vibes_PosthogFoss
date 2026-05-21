"""FOSS override: make ExportedAsset creation non-blocking.

Upstream ExportedAssetSerializer.create() calls _create_asset(force_async=False),
which uses Temporal's execute_workflow() — it blocks the Django web worker thread
until the workflow completes. On a self-hosted install with few web processes
(NGINX_UNIT_APP_PROCESSES=2), a large export holds a thread for its full duration,
blocking other users.

This subclass flips to force_async=True (start_workflow), which dispatches to
Temporal and returns 201 immediately. The frontend already polls has_content /
exception for completion status, so behaviour from the user's perspective is
identical.

Wired in via ee/urls.py:extend_api_router() which runs at URLconf load time,
before any requests. It patches ExportedAssetViewSet.serializer_class so DRF
picks up this class on every subsequent request.
"""

from posthog.api.exports import ExportedAssetSerializer
from posthog.models.exported_asset import ExportedAsset


class AsyncExportedAssetSerializer(ExportedAssetSerializer):
    def create(self, validated_data: dict, *args, **kwargs) -> ExportedAsset:
        request = self.context["request"]
        return self._create_asset(validated_data, user=request.user, reason=None, force_async=True)
