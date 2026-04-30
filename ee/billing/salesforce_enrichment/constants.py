"""FOSS stub for ee.billing.salesforce_enrichment.constants.

posthog/temporal/schedule.py and posthog/temporal/salesforce_enrichment/
import these at module load. The Temporal worker registers the workflows
but never schedules them on a FOSS deploy.
"""

DEFAULT_CHUNK_SIZE = 100
SALESFORCE_API_VERSION = "v59.0"
STRIPE_LOOKBACK_DAYS = 7
USAGE_LOOKBACK_DAYS = 30

# SOQL query upstream uses to fetch accounts due for enrichment. FOSS never
# executes it, but the workflow imports the constant at module load.
SALESFORCE_ACCOUNTS_QUERY = ""

# Salesforce field name on the Account object that stores the PostHog org id
# for joining usage / Stripe data. FOSS never reads/writes Salesforce.
POSTHOG_ORG_ID_FIELD = "PostHog_Org_ID__c"

# Page sizes for the various enrichment workflows. Values are upstream defaults
# so worker registration succeeds; the workflows themselves are never scheduled
# on a FOSS deploy.
POSTHOG_FETCH_MAPPINGS_PAGE_SIZE = 200
POSTHOG_USAGE_ENRICHMENT_BATCH_SIZE = 100
SALESFORCE_UPDATE_BATCH_SIZE = 200
STRIPE_ENRICHMENT_PAGE_SIZE = 100

# Field-mapping configs (Salesforce field -> source field). Upstream uses these
# to drive the bulk patch payload. Empty here so iteration produces no work.
POSTHOG_USAGE_FIELD_MAPPINGS: dict[str, str] = {}
STRIPE_ENRICHMENT_FIELD_MAPPINGS: dict[str, str] = {}
