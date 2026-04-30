# FOSS stub package. The upstream PostHog Enterprise Edition source tree was
# stripped from this fork. These stubs satisfy the ~200 `from ee.*` imports
# scattered across posthog/ and products/ so the app can start without ee/
# being a real Django app at runtime.
#
# Conventions:
#   - All stub functions are no-ops returning innocuous defaults.
#   - All stub classes are inert (no useful behavior).
#   - Django models have `app_label = "ee"` so migration FKs (`to="ee.role"`)
#     resolve, but they have only the minimum fields needed for OSS code to
#     not crash. Real EE feature behavior is unimplemented.
#   - posthog/settings/ee.py is patched to keep EE_AVAILABLE=False permanently
#     so EE-gated code paths in posthog/ stay disabled regardless of these
#     stubs being importable.
#
# To restore real EE features: `git checkout upstream/master -- ee/` from
# https://github.com/PostHog/posthog (subject to PostHog's EE license).
default_app_config = "ee.apps.EnterpriseConfig"
