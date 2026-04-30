"""FOSS stub. posthog/queries/column_optimizer/column_optimizer.py uses
EE column optimizer to leverage materialized columns. With EE_AVAILABLE=False,
falls back to OSS path that just reads JSON properties at query time.
"""


class EnterpriseColumnOptimizer:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("EE column optimizer unavailable in FOSS fork")


def is_property_materialized(*args, **kwargs) -> bool:
    return False
