"""FOSS stub. ChatAgentRunner orchestrates Max AI conversation runs.
Imported by posthog/temporal/ai/chat_agent.py and slack_conversation.py
(both Temporal workflows that aren't scheduled on FOSS deploys).
"""


class ChatAgentRunner:
    def __init__(self, *args, **kwargs):
        pass

    async def run(self, *args, **kwargs):
        return None
