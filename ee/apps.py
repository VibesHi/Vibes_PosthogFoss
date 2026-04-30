from django.apps import AppConfig

# Preload `products.signals.backend.temporal` to break a circular import in
# upstream PostHog that this fork's local Docker build trips during
# `manage.py collectstatic`:
#
#   posthog/api/__init__.py:30
#     → products/signals/backend/views.py:51
#       → products/signals/backend/api.py:21
#         → products/signals/backend/temporal/__init__.py
#           → deletion (line 14) → reingestion
#             → from products.signals.backend.api import emit_signal
#               ↑ api.py is still mid-load, emit_signal not yet defined → ImportError
#
# This file (ee/apps.py) is imported during settings load via
# `posthog/settings/web.py` (`from ee.apps import EnterpriseConfig`), which
# happens BEFORE `django.setup()` runs `posthog.PostHogConfig.ready()` (where
# the cycle gets triggered at line 101).
#
# Loading `products.signals.backend.temporal` eagerly here makes its
# `__init__.py` walk imports in order: agentic → backfill → buffer (line 8)
# → deletion (line 14). By the time deletion → reingestion triggers the
# api.py chain, `products.signals.backend.temporal.buffer` is already in
# sys.modules, so api.py:21's `from ... import BufferSignalsWorkflow` can
# resolve without re-entering temporal. api.py finishes, defines
# emit_signal, reingestion finds it, the whole chain unwinds cleanly.
#
# Upstream PostHog hides this bug because their hobby/cloud Docker images
# are pre-built and pulled, never running collectstatic locally.
#
# Diagnostic mode: print the full traceback so we can see what's failing.
# Once stable, replace the print/raise pair with a silent `pass`.
try:
    import products.signals.backend.temporal  # noqa: F401
except Exception:
    import sys
    import traceback

    sys.stderr.write("[ee.apps preload] signals.backend.temporal failed:\n")
    traceback.print_exc(file=sys.stderr)
    sys.stderr.flush()
    raise


class EnterpriseConfig(AppConfig):
    name = "ee"
    verbose_name = "PostHog EE (FOSS stubs)"
    default_auto_field = "django.db.models.BigAutoField"
