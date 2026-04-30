"""FOSS stub for ee.billing.dags.customer_archetype.

Imported by posthog/dags/locations/billing.py. Dagster orchestrator is not
run on a FOSS self-host (it's an internal data engineering tool), so these
schedule/job objects are defined but never invoked.
"""

from typing import Any


def customer_archetype_daily_schedule(*args, **kwargs) -> Any:
    return None


def customer_archetype_job(*args, **kwargs) -> Any:
    return None


def customer_archetype_to_clay(*args, **kwargs) -> Any:
    return None
