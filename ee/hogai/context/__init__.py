"""FOSS stub for ee.hogai.context.

products/conversations/backend/ai/mode_manager.py imports
`AssistantContextManager` from this module. Loaded only via Max AI startup
(disabled on FOSS), but the import resolves at module load time anyway.
"""


class AssistantContextManager:
    def __init__(self, *args, **kwargs):
        pass

    def get_context(self, *args, **kwargs):
        return None
