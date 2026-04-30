"""FOSS stub for ee.vercel.client.VercelAPIClient.

Used in posthog/api/organization_integration.py to talk to Vercel's API
for marketplace integrations. Inert on FOSS.
"""


class VercelAPIClient:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Vercel integration unavailable in FOSS fork")
