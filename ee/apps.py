import sys

from django.apps import AppConfig


class EnterpriseConfig(AppConfig):
    name = "ee"
    verbose_name = "PostHog EE (FOSS stubs)"
    default_auto_field = "django.db.models.BigAutoField"

    def __init__(self, app_name: str, app_module) -> None:
        """
        Django calls AppConfig.__init__ during phase 1 of apps.populate()
        (app-config instantiation), AFTER settings have fully loaded and
        BEFORE any AppConfig.ready() (phase 3) fires.

        We use this window to monkey-patch `posthog.PostHogConfig.ready` so
        that, when Django invokes posthog's ready() in phase 3 (which is
        BEFORE our own ready() because `ee.apps.EnterpriseConfig` is
        appended at the end of INSTALLED_APPS), posthog's ready() FIRST
        loads `products.signals.backend.temporal` to completion. That walks
        temporal/__init__.py in source order (agentic → backfill → buffer
        at line 8 → deletion at line 14), and by the time
        deletion → reingestion → api.py loads, `temporal.buffer` is already
        in sys.modules so api.py:21's
        `from .temporal.buffer import BufferSignalsWorkflow` resolves
        cleanly. Then the original ready() runs, hitting cached sys.modules
        entries instead of the broken-circular-import path at line 101
        (`from posthog.api.file_system import registrations`).

        This breaks an upstream-PostHog circular import that local Docker
        builds trip during `manage.py collectstatic`:

            posthog/api/__init__.py:30
              → products/signals/backend/views.py:51
                → products/signals/backend/api.py:21
                  → products/signals/backend/temporal/__init__.py
                    → deletion (line 14) → reingestion (line 19)
                      → from products.signals.backend.api import emit_signal
                        ↑ api.py is still mid-load → ImportError

        Upstream PostHog hides this bug because hobby/cloud Docker images
        are pre-built (collectstatic runs in PostHog's CI against the full
        EE tree, where module load order ends up different) and pulled,
        never running collectstatic locally.

        Why __init__ and not ready():
            - posthog.PostHogConfig.ready() runs BEFORE
              EnterpriseConfig.ready() in phase 3 (posthog comes before ee
              in INSTALLED_APPS order). By the time our ready() fires, the
              cycle has already exploded.
            - __init__ runs in phase 1 (in INSTALLED_APPS order, so ours is
              last too) but ALL phase-1 __init__'s complete BEFORE any
              phase-2 / phase-3 work begins, giving us a clean window.

        Done from `ee/apps.py` only -- no edits to upstream `products/` or
        `posthog/` trees, so this fork stays diff-clean against master.

        Wrapped in defensive try/except: failure here means the cycle
        re-fires at the original site (visible and recoverable) rather
        than a hard-to-diagnose silent failure of the `ee` app config.
        """
        super().__init__(app_name, app_module)
        try:
            self._patch_posthog_ready_for_signals_preload()
        except Exception as exc:
            sys.stderr.write(
                f"[ee.apps] could not patch posthog ready() for signals preload: {exc!r}\n"
            )

    @staticmethod
    def _patch_posthog_ready_for_signals_preload() -> None:
        # posthog.apps is guaranteed to be in sys.modules here: phase 1
        # processes apps in INSTALLED_APPS order, and posthog comes well
        # before ee, so PostHogConfig has already been imported and
        # instantiated by the time our __init__ runs.
        from posthog.apps import PostHogConfig

        if getattr(PostHogConfig.ready, "_ee_signals_preload_patched", False):
            return

        _original_ready = PostHogConfig.ready

        def patched_ready(self, *args, **kwargs):
            try:
                import products.signals.backend.temporal  # noqa: F401
            except Exception as exc:
                sys.stderr.write(
                    f"[ee.apps] signals.backend.temporal preload failed; "
                    f"posthog.PostHogConfig.ready() may now hit the upstream "
                    f"reingestion → api.py circular import: {exc!r}\n"
                )
            return _original_ready(self, *args, **kwargs)

        patched_ready._ee_signals_preload_patched = True  # type: ignore[attr-defined]
        PostHogConfig.ready = patched_ready  # type: ignore[method-assign]
