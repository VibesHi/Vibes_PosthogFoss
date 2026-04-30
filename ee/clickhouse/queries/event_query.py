"""FOSS stub. posthog/queries/event_query/__init__.py overrides OSS EventQuery
with this enterprise variant. EE_AVAILABLE=False keeps OSS path active.
"""


class EnterpriseEventQuery:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("EE event query unavailable in FOSS fork")


# posthog/queries/event_query/__init__.py also imports the unaliased name.
EventQuery = EnterpriseEventQuery
