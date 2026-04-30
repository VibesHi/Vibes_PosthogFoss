"""FOSS stub for ee.hogai.stream.redis_stream.

Used by Temporal AI workflows + tests. Real upstream streams LLM tokens to
the frontend via Redis; FOSS has no AI so the stream is unused.
"""

CONVERSATION_STREAM_PREFIX = "@posthog/conversation-stream/"


class ConversationRedisStream:
    """No-op. Never instantiated on FOSS since Max AI is disabled."""

    def __init__(self, *args, **kwargs):
        pass

    async def stream(self, *args, **kwargs):
        return None

    async def publish(self, *args, **kwargs):
        return None


def get_conversation_stream_key(conversation_id: str = "", *args, **kwargs) -> str:
    return f"{CONVERSATION_STREAM_PREFIX}{conversation_id}"
