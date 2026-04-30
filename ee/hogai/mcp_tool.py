"""FOSS stub. Used by products/posthog_ai/backend/api/mcp_tools.py."""


class _MCPToolRegistry:
    """Empty tool registry for the MCP (Model Context Protocol) server."""

    def register(self, *args, **kwargs):
        return None

    def get_all(self) -> list:
        return []


mcp_tool_registry = _MCPToolRegistry()
