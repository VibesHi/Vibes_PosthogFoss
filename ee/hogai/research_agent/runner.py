"""FOSS stub for ee.hogai.research_agent.runner.ResearchAgentRunner."""


class ResearchAgentRunner:
    """No-op. Used by posthog/temporal/ai/research_agent.py (Temporal workflow,
    not invoked on FOSS deploy)."""

    def __init__(self, *args, **kwargs):
        pass

    async def run(self, *args, **kwargs):
        return None
