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
