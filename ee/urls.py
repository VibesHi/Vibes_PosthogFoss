# FOSS stub: posthog/urls.py wraps `from ee.urls import extend_api_router,
# urlpatterns` in a try/except ImportError that falls back to no extra URLs.
# Once we expose this stub, the import succeeds and extend_api_router() is
# called -- so it must be a no-op.

urlpatterns: list = []


def extend_api_router(*args, **kwargs) -> None:
    """No-op: EE adds extra DRF routes here in upstream. FOSS has none."""
    return None
