"""FOSS stub for ee.hogai.utils.types.base."""

from dataclasses import dataclass, field
from typing import Any

from ee._stubs import PermissiveConstants


class AssistantGraphName(PermissiveConstants):
    """Identifiers for the different agent loop graphs upstream registers.
    Permissive: any `.X` access returns "x"."""

    DEFAULT = "default"
    RESEARCH = "research"
    CHAT = "chat"
    SUPPORT = "support"


class AssistantNodeName(PermissiveConstants):
    """Identifiers for graph nodes. Upstream has ~30 specific values;
    permissive metaclass handles drift without explicit listing."""

    START = "start"
    END = "end"
    ROOT = "root"
    ROOT_TOOLS = "root_tools"
    TOOLS = "tools"
    AGENT = "agent"
    HUMAN = "human"
    WEB_ANALYTICS_FILTER = "web_analytics_filter"
    WEB_ANALYTICS_FILTER_OPTIONS_TOOLS = "web_analytics_filter_options_tools"


@dataclass
class AssistantState:
    """Re-exported here because some imports use base directly."""

    messages: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextMessage:
    """Message wrapper used by the chat-agent runner."""

    role: str = ""
    content: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# Re-export NodePath at this path too.
NodePath = list[str]
