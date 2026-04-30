# FOSS stub: posthog/settings/__init__.py does `from ee.settings import *`
# when ee.apps.EnterpriseConfig is in INSTALLED_APPS. We register the app for
# the Django ORM, so that `import *` runs. Keep settings here trivial.

# Used by products/tasks/backend/seat_api.py and a few other places. Empty
# string disables outbound calls to the PostHog billing service.
BILLING_SERVICE_URL = ""
