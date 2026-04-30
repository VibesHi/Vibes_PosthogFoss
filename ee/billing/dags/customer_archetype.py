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


# posthog/dags/locations/billing.py imports these names at module top.
# Real upstream defines them as Dagster JobDefinition / ScheduleDefinition /
# AssetsDefinition objects. Self-host doesn't run Dagster, so None is fine --
# Dagster's loader skips missing/None-typed entries instead of crashing.
archetype_account_data: Any = None
archetype_classify_and_sync: Any = None
archetype_job: Any = None
archetype_weekly_schedule: Any = None
