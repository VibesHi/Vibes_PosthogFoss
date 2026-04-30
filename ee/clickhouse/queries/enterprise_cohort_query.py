"""FOSS stub. posthog/queries/cohort_query.py uses
`from ee.clickhouse.queries.enterprise_cohort_query import EnterpriseCohortQuery as CohortQuery`
to override the OSS CohortQuery when EE is enabled. With EE_AVAILABLE=False,
this branch is dead.
"""


class EnterpriseCohortQuery:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("EE cohort query unavailable in FOSS fork")
