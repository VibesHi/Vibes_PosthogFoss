"""FOSS stub for ee.billing.dags.productled_outbound_targets."""

from typing import Any


def productled_outbound_targets_daily_schedule(*args, **kwargs) -> Any:
    return None


def productled_outbound_targets_job(*args, **kwargs) -> Any:
    return None


def productled_outbound_targets_to_clay(*args, **kwargs) -> Any:
    return None


# posthog/dags/locations/billing.py imports these names at module top.
# Real upstream defines them as Dagster JobDefinition / ScheduleDefinition /
# AssetsDefinition objects. Self-host doesn't run Dagster.
plo_base_targets: Any = None
plo_daily_schedule: Any = None
plo_job: Any = None
plo_qualified_to_clay: Any = None
qualify_signals: Any = None
