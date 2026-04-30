"""FOSS stub for ee.hogai.llm.

MaxChatOpenAI wraps langchain_openai.ChatOpenAI in upstream. Used by
products/*/backend/max_tools.py files which are loaded only when Max AI
discovery runs -- which it doesn't on FOSS (no AI agent loop).
"""


class MaxChatOpenAI:
    """No-op stand-in. Instantiating it raises so accidental use is loud."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Max AI assistant unavailable in FOSS fork")


class MaxChatAnthropic:
    """Same pattern for the Anthropic path used by anomaly_investigation runner."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Max AI assistant unavailable in FOSS fork")
