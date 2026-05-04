"""FOSS stub viewsets for endpoints stripped behind `if EE_AVAILABLE:` in
`posthog/api/__init__.py`.

These viewsets are wired into the API router by `ee/urls.py:extend_api_router()`,
which runs once at URLconf load time AFTER `posthog/api/__init__.py` has built
its routers. Registration order is therefore guaranteed to be AFTER the
upstream OSS routes, which matters because DRF emits URL patterns in
registration order and our wildcard `<pk>` regexes could otherwise shadow
more-specific upstream prefixes.

Each stub returns an empty paginated list for `list()` and 404 for any other
method. This is the minimum to silence kea `loaders` plugin "Load X failed"
toasts that fire on scene mount when the frontend's React app reaches an
EE-only endpoint that was never registered.

POST/PATCH/DELETE intentionally fail loudly (405/404). There's no UI to
write Experiments, Groups, etc. on FOSS, and silently accepting writes
would let a user think they're creating a real EE resource.
"""

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

EMPTY_PAGINATED: dict = {"count": 0, "next": None, "previous": None, "results": []}


class _EmptyListStubViewSet(viewsets.ViewSet):
    """Plain DRF ViewSet -- intentionally NOT TeamAndOrgViewSetMixin.

    The mixin requires a queryset and adds team-scoped filter logic we don't
    need (we never touch the DB). Default `posthog.auth.SessionAuthentication`
    + `IsAuthenticated` from `REST_FRAMEWORK` settings still apply, so these
    endpoints stay session-gated like every other authenticated endpoint.
    Anonymous requests get 401, not the empty list.
    """

    # Marker for the schema generator / scope checker -- "INTERNAL" means
    # not exposed via personal API keys, only via session cookies. Matches
    # how upstream tags non-public endpoints.
    scope_object = "INTERNAL"

    def list(self, request, *args, **kwargs) -> Response:
        return Response(EMPTY_PAGINATED)

    def retrieve(self, request, *args, **kwargs) -> Response:
        return Response(status=status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# Max AI assistant
# ---------------------------------------------------------------------------


class MaxConversationStubViewSet(_EmptyListStubViewSet):
    """Stub for `/api/environments/<id>/conversations/`.

    Frontend's `maxGlobalLogic.tsx:loadConversationHistory` calls this on
    mount of the Max chat sidebar (which lives on every page). Returning
    empty list makes the sidebar render in "no conversations yet" state
    instead of toasting "Load conversation history failed".
    """


class CoreMemoryStubViewSet(_EmptyListStubViewSet):
    """Stub for `/api/environments/<id>/core_memory/`.

    Frontend's `maxSettingsLogic.tsx:loadCoreMemory` reads `response.results[0]`
    and falls back to `null` if missing -- exactly what we return.
    """


# ---------------------------------------------------------------------------
# Experiments (EE-gated in posthog/api/__init__.py:804)
# ---------------------------------------------------------------------------


class ExperimentsStubViewSet(_EmptyListStubViewSet):
    """Stub for `/api/projects/<id>/experiments/`.

    Triggers from `experimentsLogic.ts` on visit to `/experiments` scene:
        - GET /experiments?<filters> (loadExperiments)
        - GET /experiments/stats/ (loadExperimentsStats, fired in afterMount)
        - GET /experiments/eligible_feature_flags/?<params>

    Listing returns empty -> scene shows "Create your first experiment"
    empty state. The stats action returns zeros so the "Velocity"
    widget renders without toasting. eligible_feature_flags returns empty
    so the create-experiment modal doesn't crash the moment it mounts
    (though create POST will 405 since we don't define `create`).
    """

    @action(detail=False, methods=["get"])
    def stats(self, request, *args, **kwargs) -> Response:
        # Shape mirrors `ExperimentVelocityStats` in
        # frontend/src/scenes/experiments/experimentsLogic.ts (afterMount default).
        return Response(
            {
                "launched_previous_30d": 0,
                "percent_change": 0,
                "active_experiments": 0,
                "completed_last_30d": 0,
            }
        )

    @action(detail=False, methods=["get"])
    def eligible_feature_flags(self, request, *args, **kwargs) -> Response:
        # Used by the "create experiment from existing flag" modal.
        return Response(EMPTY_PAGINATED)


class ExperimentHoldoutsStubViewSet(_EmptyListStubViewSet):
    """Stub for `/api/projects/<id>/experiment_holdouts/`.

    Holdouts are scoped to the experiment detail page, which is unreachable
    on FOSS (Experiments list is empty). Stubbing list() defensively in
    case some other logic preloads holdouts globally.
    """


class ExperimentSavedMetricsStubViewSet(_EmptyListStubViewSet):
    """Stub for `/api/projects/<id>/experiment_saved_metrics/`. Same reasoning
    as ExperimentHoldoutsStubViewSet."""


# ---------------------------------------------------------------------------
# Group analytics (EE-gated in posthog/api/__init__.py:804)
# ---------------------------------------------------------------------------


class GroupsTypesStubViewSet(_EmptyListStubViewSet):
    """Stub for `/api/projects/<id>/groups_types/`.

    Frontend's `groupsModel.ts` loads this on every authenticated page mount
    (it's a "global" kea logic, not gated by scene). Without a route the
    kea-loaders plugin emits a "Load all group types failed: Endpoint not
    found." toast on every navigation.

    Upstream returns a FLAT array (pagination_class = None on the real
    viewset), not a paginated envelope. Frontend code does
    `groupTypes = response` directly, so we override list() to skip
    EMPTY_PAGINATED. GroupTypeMapping is technically an OSS model and we
    *could* wire a real read-only viewset, but the fork isn't shipping
    group analytics UI, so empty list is the honest answer.
    """

    def list(self, request, *args, **kwargs) -> Response:
        return Response([])


# ---------------------------------------------------------------------------
# Billing (EE-only feature; `/api/billing/` lives in `ee.billing.api` upstream)
# ---------------------------------------------------------------------------


class BillingStubViewSet(viewsets.ViewSet):
    """Stub for `/api/billing/`.

    Frontend's `billingLogic.tsx:loadBilling` is `lazyLoaders` -- only fires
    when a component selecting `values.billing` mounts (Settings -> Billing,
    org-wide usage banners). On a vanilla self-host with the synthetic
    license, billing UI shouldn't even be reachable, but several global
    components (e.g. usage-limit banners, organization switcher) read
    `billing.subscription_level` defensively. A 404 there causes a kea
    error toast.

    Returns a free-tier-shaped envelope with empty product/plan arrays.
    `parseBillingResponse` accepts `Partial<BillingType>` and dayjs-coerces
    only what it finds, so omitted fields are fine.

    Note: this is a `ViewSet` (not nested under team/org), so it's
    registered against the root `router`, not `projects_router`.
    """

    scope_object = "INTERNAL"

    def list(self, request, *args, **kwargs) -> Response:
        return Response(
            {
                "available_plans": [],
                "products": [],
                "subscription_level": "free",
                "has_active_subscription": False,
                "license": None,
                "stripe_portal_url": None,
                "billing_period": None,
                "current_total_amount_usd": "0.00",
                "customer_id": None,
            }
        )

    def update(self, request, *args, **kwargs) -> Response:
        # Frontend tries to PATCH custom_limits_usd. No-op + return current state.
        return self.list(request)

    def partial_update(self, request, *args, **kwargs) -> Response:
        return self.list(request)
