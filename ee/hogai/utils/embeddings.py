"""FOSS stub. Used by posthog/temporal/ai/sync_vectors.py.

Real upstream produces text embeddings via Azure OpenAI for the AI vector
search index. FOSS: no embeddings, no vector search.
"""

from typing import Any


async def aembed_documents(*args, **kwargs) -> list[list[float]]:
    return []


def get_async_azure_embeddings_client(*args, **kwargs) -> Any:
    return None
