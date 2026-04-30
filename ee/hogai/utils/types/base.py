"""FOSS stub for ee.hogai.utils.types.base."""

from enum import Enum


class AssistantGraphName(str, Enum):
    """Identifiers for the different agent loop graphs upstream registers."""

    DEFAULT = "default"
    RESEARCH = "research"
    CHAT = "chat"


# Re-export NodePath at this path too, since some imports use base directly.
NodePath = list[str]
