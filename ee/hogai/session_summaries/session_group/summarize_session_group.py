"""FOSS stub for ee.hogai.session_summaries.session_group.summarize_session_group."""

from typing import Any


def summarize_session_group(*args, **kwargs) -> Any:
    return None


def execute_summarize_session_group(*args, **kwargs) -> Any:
    return None


def generate_session_group_patterns_extraction_prompt(*args, **kwargs) -> str:
    return ""


def generate_session_group_patterns_assignment_prompt(*args, **kwargs) -> str:
    return ""


def generate_session_group_patterns_combination_prompt(*args, **kwargs) -> str:
    return ""


def remove_excessive_content_from_session_summary_for_llm(*args, **kwargs) -> Any:
    """Upstream trims large fields (events, screenshots) before sending to the
    LLM. FOSS: pass-through (the activity that calls this never runs)."""
    if args:
        return args[0]
    return None
