"""FOSS stub for ee.hogai.chat_agent.schema_generator.parsers."""


from typing import Any


class PydanticOutputParserException(Exception):
    """Raised by upstream's pydantic LLM-output parser. Never raised on FOSS."""

    pass


def parse_pydantic_structured_output(*args, **kwargs) -> Any:
    """Upstream coerces an LLM JSON blob into a pydantic model. FOSS: no-op."""
    return None
