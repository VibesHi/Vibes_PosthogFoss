"""FOSS stub. Used in products/conversations/backend/ai/prompt_builder.py."""


def format_prompt_string(template: str = "", *args, **kwargs) -> str:
    """Real upstream renders a Jinja-like template. FOSS returns input as-is."""
    return template
