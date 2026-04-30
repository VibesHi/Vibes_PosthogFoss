"""FOSS stub for ee.vercel.integration.VercelIntegration.

Used by posthog/tasks/integrations.py to sync Vercel-managed PostHog
projects. FOSS: no Vercel marketplace, the integration is inert.
"""


class VercelIntegration:
    """No-op. Instantiating raises so accidental use is loud."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Vercel integration unavailable in FOSS fork")

    def sync(self, *args, **kwargs) -> None:
        return None
