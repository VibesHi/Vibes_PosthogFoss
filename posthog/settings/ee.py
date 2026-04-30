# Hard-coded False for the FOSS fork. The ee/ package still exists in this
# repo as no-op stubs (see ee/__init__.py for the rationale) so that:
#
#   1. Django can register `ee.apps.EnterpriseConfig` in INSTALLED_APPS
#      (posthog/settings/web.py:160). This is required because 9 migrations
#      across posthog/ and products/ have FK refs like `to="ee.role"` that
#      need the `ee` app present for resolution.
#   2. Unconditional `from ee.X import Y` statements in posthog/api/__init__.py
#      and other hot files don't crash at module load.
#
# But EE_AVAILABLE is the SEPARATE knob that gates EE *features* (Vercel API,
# enterprise viewsets, materialized columns, scheduled subscriptions, RBAC
# enforcement, etc.) inside `if EE_AVAILABLE:` branches. Forcing it False
# keeps those branches dead regardless of ee/ being importable, so EE-stubbed
# code paths don't get exercised by accident.
#
# To restore real EE features: replace ee/ with upstream
# (`git checkout upstream/master -- ee/`) AND replace this file with the
# upstream version that does the auto-detection import.
EE_AVAILABLE = False
