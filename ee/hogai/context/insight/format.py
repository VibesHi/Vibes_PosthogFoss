"""FOSS stub for ee.hogai.context.insight.format.

posthog/api/query.py imports this lazily inside one method branch that
formats query results for an LLM consumer. With Max AI disabled, that branch
is never reached, but defensive empty-string return makes accidents safe.
"""

from typing import Any


def format_query_results_for_llm(*args, **kwargs) -> str:
    """Returns an empty LLM context. Real upstream renders results as markdown."""
    return ""


def format_insight_for_llm(*args, **kwargs) -> str:
    return ""


def format_dashboard_for_llm(*args, **kwargs) -> str:
    return ""
