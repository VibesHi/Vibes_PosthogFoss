"""FOSS overlay for upstream `ee/urls.py`.

`posthog/urls.py:77-87` does:

    try:
        from ee.urls import extend_api_router, urlpatterns as ee_urlpatterns
    except ImportError:
        ...
    else:
        extend_api_router()

So this module must export both names. `extend_api_router()` is called once
at URLconf load time, AFTER `posthog/api/__init__.py` has built the routers,
which gives us a deterministic hook to register fork-only viewsets without
patching upstream files.

What we register here is intentionally narrow: stubs for endpoints stripped
behind `if EE_AVAILABLE:` whose frontend siblings are NOT gated by the same
flag and would 404 (with a kea-loader toast) on user navigation. Real EE
features stay disabled.
"""

from ee.api.foss_stubs import (
    CoreMemoryStubViewSet,
    ExperimentHoldoutsStubViewSet,
    ExperimentSavedMetricsStubViewSet,
    ExperimentsStubViewSet,
    MaxConversationStubViewSet,
)

urlpatterns: list = []


def extend_api_router() -> None:
    # Lazy import: posthog.api builds these routers at module-load time, but
    # importing posthog.api from the top of ee/urls.py would pull in the
    # entire DRF route table during Django app loading, which is fragile.
    # By the time extend_api_router() is called from posthog/urls.py the
    # routers are guaranteed to exist.
    from posthog.api import environments_router, projects_router

    # IMPORTANT: registration order vs. existing `conversations/tickets` and
    # `conversations/views` (registered earlier in posthog/api/__init__.py).
    # DRF emits URL patterns in registration order; Django resolves them
    # top-to-bottom. Our `r"conversations"` registration happens LAST (because
    # extend_api_router runs after posthog.api finishes loading), so:
    #   /api/environments/<id>/conversations/tickets/  -> TicketViewSet  (matched first)
    #   /api/environments/<id>/conversations/views/    -> TicketViewViewSet (matched first)
    #   /api/environments/<id>/conversations/          -> our stub
    #   /api/environments/<id>/conversations/<uuid>/   -> our stub.retrieve -> 404
    # If you ever re-order this so our register() runs first, the wildcard
    # `conversations/<pk>` pattern WILL shadow `conversations/tickets`.
    environments_router.register(
        r"conversations",
        MaxConversationStubViewSet,
        "environment_max_conversations_stub",
        ["team_id"],
    )

    environments_router.register(
        r"core_memory",
        CoreMemoryStubViewSet,
        "environment_core_memory_stub",
        ["team_id"],
    )

    # Experiments scene (`/experiments`) hits all three of these on mount.
    # Stubbing list endpoints lets the scene render the empty-state UI
    # instead of toasting. The `/stats/` and `/eligible_feature_flags/`
    # custom actions are handled inside ExperimentsStubViewSet.
    projects_router.register(
        r"experiments",
        ExperimentsStubViewSet,
        "project_experiments_stub",
        ["project_id"],
    )

    projects_router.register(
        r"experiment_holdouts",
        ExperimentHoldoutsStubViewSet,
        "project_experiment_holdouts_stub",
        ["project_id"],
    )

    projects_router.register(
        r"experiment_saved_metrics",
        ExperimentSavedMetricsStubViewSet,
        "project_experiment_saved_metrics_stub",
        ["project_id"],
    )
