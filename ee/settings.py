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


# === Generic fallback for `try: from ee import settings; settings.X; except ImportError` ===
#
# Upstream's EE-vs-FOSS gate uses two interchangeable idioms:
#
#     try:
#         from ee.X import Y
#     except ImportError:
#         Y = ...fallback...
#
#     try:
#         from ee import settings
#         value = settings.SOME_EE_FEATURE_FLAG
#     except ImportError:
#         value = ...fallback...
#
# The first works in our fork because deleting `ee/X.py` raises ImportError.
# The second BREAKS because we kept `ee/` as importable stubs (see
# `ee/__init__.py` for why) -- `from ee import settings` succeeds, then
# `settings.SOME_EE_FEATURE_FLAG` raises AttributeError, which `except
# ImportError` doesn't catch, and the request 500s. This was the SSO+2FA
# regression that fanned out into Load-dashboards/Load-annotations/etc errors.
#
# PEP 562 module __getattr__ reroutes unknown-attribute access on this module
# to ImportError, which makes every "try: from ee import settings; settings.X;
# except ImportError: fallback" upstream wrote work as the upstream authors
# originally intended -- without us having to pre-declare every EE-only
# setting upstream might add in a future sync.
#
# Tradeoffs:
#   - `from ee.settings import *` (line ~123 of posthog/settings/__init__.py)
#     uses dir()/__all__, NOT __getattr__, so the wildcard import is
#     unaffected: only the explicitly-defined names above leak into Django
#     settings, AUTHENTICATION_BACKENDS does NOT clobber the OSS list.
#   - `getattr(ee.settings, "X", default)` will trigger __getattr__ instead
#     of returning the default. Code that relies on this gets ImportError
#     instead -- audit if you see a NEW `getattr(...)` traceback.
#   - Typos in OUR code that look up `ee.settings.MISSPELLED` get
#     ImportError, not AttributeError. Acceptable: the message names the
#     missing attribute clearly.
def __getattr__(name: str):
    # Dunder + private leading-underscore names: real Python attribute lookup
    # mistakes; surface as AttributeError to match builtin module behavior.
    if name.startswith("_"):
        raise AttributeError(name)
    raise ImportError(
        f"ee.settings.{name} is not declared in the FOSS stub. "
        f"This is benign IF the calling code does `try: ...; except ImportError: ...` "
        f"(the upstream pattern for EE-vs-FOSS gating). "
        f"If you see this surfacing as a 500, add an `except ImportError` to the call site "
        f"OR an explicit safe default for {name} above this __getattr__."
    )
