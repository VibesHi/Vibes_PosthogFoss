"""FOSS stub for ee.hogai.core.loop_graph.graph.AgentLoopGraph."""


class AgentLoopGraph:
    """No-op. Used as base class for products/conversations/backend/ai/graph.py
    which is part of the Conversations product's AI runner -- never invoked on FOSS."""

    def __init__(self, *args, **kwargs):
        pass

    def compile(self, *args, **kwargs):
        return self
