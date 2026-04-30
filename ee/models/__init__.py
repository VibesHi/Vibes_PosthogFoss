# Re-exports for `from ee.models import X` convenience imports used in OSS code.
# Production paths only -- test-only imports (e.g. EnterprisePropertyDefinition
# in posthog/models/test/test_tagged_item_model.py) are also re-exported
# defensively so test files don't crash even though they're not loaded by the
# production containers.

# Preload `products.signals.backend.temporal` to break an upstream-PostHog
# circular import that this fork's local Docker build trips during
# `manage.py collectstatic`:
#
#   posthog/api/__init__.py:30 (during posthog.PostHogConfig.ready)
#     → products/signals/backend/views.py:51
#       → products/signals/backend/api.py:21
#         → products/signals/backend/temporal/__init__.py
#           → deletion (line 14) → reingestion (line 19)
#             → from products.signals.backend.api import emit_signal
#               ↑ api.py is still mid-load → ImportError
#
# Why here? `ee/models/__init__.py` is loaded by Django's `apps.populate()`
# during phase 2 (import_models), in INSTALLED_APPS order. `ee` is appended
# last, so by the time we run, settings are fully loaded AND every other
# app's models (including posthog and products.signals.backend) are loaded
# too. Phase 3 (`AppConfig.ready()`) hasn't started yet, so
# posthog.PostHogConfig.ready() (where the cycle would fire at line 101)
# hasn't run.
#
# The preload itself walks temporal/__init__.py in source order: agentic →
# backfill → buffer (line 8) → deletion (line 14). When deletion →
# reingestion triggers loading api.py, api.py:21 needs
# temporal.buffer.BufferSignalsWorkflow — already in sys.modules from line 8
# above, so the lookup resolves and api.py finishes, defining emit_signal.
# Cycle dissolved, and posthog.api/__init__.py also gets fully loaded as a
# side effect of reingestion.py:15 (`from posthog.api.embedding_worker import
# emit_embedding_request`), so the actual `posthog.PostHogConfig.ready()`
# call later just hits cached sys.modules entries.
#
# Upstream PostHog hides this bug because hobby/cloud Docker images are
# pre-built (collectstatic ran in PostHog's CI against the full EE tree) and
# pulled, never running collectstatic locally.
#
# Diagnostic: log when this preload runs and what state Django is in. If we
# see the cycle still firing in collectstatic, the trace below tells us
# whether (a) we ran too early (settings still mid-load) or (b) we ran but
# something else short-circuited the preload.
import sys as _sys

_sys.stderr.write("[ee.models preload] starting; will load products.signals.backend.temporal\n")
try:
    from django.apps import apps as _apps

    _sys.stderr.write(
        f"[ee.models preload] apps_ready={_apps.apps_ready} models_ready={_apps.models_ready} ready={_apps.ready}\n"
    )
except Exception as _e:
    _sys.stderr.write(f"[ee.models preload] could not read apps state: {_e!r}\n")
_sys.stderr.flush()

try:
    import products.signals.backend.temporal  # noqa: F401

    _sys.stderr.write("[ee.models preload] OK: temporal preloaded\n")
    _sys.stderr.flush()
except Exception:
    import traceback

    _sys.stderr.write("[ee.models preload] FAILED:\n")
    traceback.print_exc(file=_sys.stderr)
    _sys.stderr.flush()
    raise

from ee.models.conversation import Conversation
from ee.models.dashboard_privilege import DashboardPrivilege
from ee.models.event_definition import EnterpriseEventDefinition
from ee.models.property_definition import EnterprisePropertyDefinition
from ee.models.rbac.access_control import AccessControl
from ee.models.rbac.role import Role, RoleMembership

__all__ = [
    "AccessControl",
    "Conversation",
    "DashboardPrivilege",
    "EnterpriseEventDefinition",
    "EnterprisePropertyDefinition",
    "Role",
    "RoleMembership",
]
