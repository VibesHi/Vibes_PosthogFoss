"""FOSS stub. Conversation queue persistence used by Max AI."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ConversationQueueMessage:
    conversation_id: str = ""
    payload: Any = None


class ConversationQueueStore:
    def __init__(self, *args, **kwargs):
        pass

    async def push(self, *args, **kwargs):
        return None

    async def pop(self, *args, **kwargs):
        return None
