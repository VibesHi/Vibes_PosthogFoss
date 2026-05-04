"""FOSS port of upstream `ee/settings.py`.

Loaded by `posthog/settings/__init__.py` via:
    if "ee.apps.EnterpriseConfig" in INSTALLED_APPS:
        from ee.settings import *

Upstream uses this file to declare every Django setting whose access
pattern is `settings.X` but whose code lives behind EE features (billing,
SAML, Customer.io, materialization, AI assistants, etc.). When the
settings are missing, `settings.X` raises AttributeError at runtime --
which is what crashed `run_async_migrations --complete-noop-migrations`
on first install (CUSTOMER_IO_API_KEY).

We mirror upstream's *names and defaults* one-for-one so:
  1. Code paths that touch these settings short-circuit cleanly on FOSS
     (empty string / disabled flag / sensible defaults).
  2. Future merges from upstream don't introduce drift in this file.
  3. Operators can override any of them via env vars without code changes.

What's INTENTIONALLY OMITTED vs. upstream:
  * SAML / Google OAuth backends -- adding them to AUTHENTICATION_BACKENDS
    pulls in `ee.api.authentication` which is a stripped EE module.
    SSO is an EE-only feature here.

If something else breaks with `AttributeError: 'Settings' object has no
attribute X`, add X here with a safe default rather than scattering
defaults across `posthog/settings/*` -- keeps the EE/FOSS boundary at
one file, matching upstream layout.
"""

from posthog.settings.base_variables import DEBUG, DEMO
from posthog.settings.utils import get_from_env, str_to_bool
from posthog.settings.web import MIDDLEWARE as _BASE_MIDDLEWARE

# Append fork-only middleware. Loaded LAST in posthog/settings/__init__.py via
# `from ee.settings import *`, so this overrides the upstream MIDDLEWARE list
# without touching posthog/settings/web.py.
#
# Order: appended at the END so it runs after upstream middlewares finished
# building the response. Preflight middleware only mutates body bytes for
# /_preflight, so it's safe to sit anywhere in the chain.
MIDDLEWARE = [*_BASE_MIDDLEWARE, "ee.middleware.PreflightKafkaProbeMiddleware"]

# Customer.io HTTP-API email integration. Empty -> disabled; SMTP fallback
# in posthog/email.py still works if EMAIL_HOST is set via instance settings.
CUSTOMER_IO_API_KEY = get_from_env("CUSTOMER_IO_API_KEY", "", type_cast=str)

# Schedule + tuning knobs for the materialize_columns Celery task. On FOSS
# we don't auto-materialize (the EE materialize() ALTER TABLE helper is a
# no-op stub), but `posthog/tasks/scheduled.py` still reads the cron to
# register the beat schedule -- it'd just no-op when fired.
MATERIALIZE_COLUMNS_SCHEDULE_CRON = get_from_env("MATERIALIZE_COLUMNS_SCHEDULE_CRON", "0 5 * * SAT")
MATERIALIZE_COLUMNS_MINIMUM_QUERY_TIME = get_from_env("MATERIALIZE_COLUMNS_MINIMUM_QUERY_TIME", 40000, type_cast=int)
MATERIALIZE_COLUMNS_ANALYSIS_PERIOD_HOURS = get_from_env(
    "MATERIALIZE_COLUMNS_ANALYSIS_PERIOD_HOURS", 7 * 24, type_cast=int
)
MATERIALIZE_COLUMNS_BACKFILL_PERIOD_DAYS = get_from_env("MATERIALIZE_COLUMNS_BACKFILL_PERIOD_DAYS", 0, type_cast=int)
MATERIALIZE_COLUMNS_MAX_AT_ONCE = get_from_env("MATERIALIZE_COLUMNS_MAX_AT_ONCE", 100, type_cast=int)

# Used by posthog/admin/admins/organization_admin.py:197 to render an
# admin link to the billing dashboard. Empty disables the link.
BILLING_SERVICE_URL = get_from_env("BILLING_SERVICE_URL", "")

# Django admin portal toggle. Default off in production for security
# (admin credentials are a high-value target on a public host).
ADMIN_PORTAL_ENABLED = get_from_env("ADMIN_PORTAL_ENABLED", DEMO or DEBUG, type_cast=str_to_bool)

PARALLEL_ASSET_GENERATION_MAX_TIMEOUT_MINUTES = get_from_env(
    "PARALLEL_ASSET_GENERATION_MAX_TIMEOUT_MINUTES", 10.0, type_cast=float
)

# Comma-separated team IDs allow-listed for the hog-function hook
# integration. Empty -> hook fires for no teams (FOSS default).
HOOK_HOG_FUNCTION_TEAMS = get_from_env("HOOK_HOG_FUNCTION_TEAMS", "", type_cast=str)

# Langfuse (LLM observability) -- read by AI assistant / LLM analytics
# code paths. Blank values disable Langfuse calls entirely.
LANGFUSE_PUBLIC_KEY = get_from_env("LANGFUSE_PUBLIC_KEY", "", type_cast=str)
LANGFUSE_SECRET_KEY = get_from_env("LANGFUSE_SECRET_KEY", "", type_cast=str)
LANGFUSE_HOST = get_from_env("LANGFUSE_HOST", "https://us.cloud.langfuse.com", type_cast=str)

# Anthropic API key for the LLM analytics / signals / posthog_ai dags.
# Code paths in products/llm_analytics, products/signals, products/posthog_ai
# all access via settings.ANTHROPIC_API_KEY; empty means "not configured" and
# those features short-circuit to either a no-op or an explicit "AI not
# configured" error at call site.
ANTHROPIC_API_KEY = get_from_env("ANTHROPIC_API_KEY", "")
