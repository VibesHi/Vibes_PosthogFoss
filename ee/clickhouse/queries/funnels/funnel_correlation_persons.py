"""FOSS stub. Used in posthog/api/person.py for the funnel-correlation persons
endpoint (EE feature). Lazy import inside a method; never reached on FOSS.
"""


class FunnelCorrelationActors:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("EE funnel correlation persons unavailable in FOSS fork")
