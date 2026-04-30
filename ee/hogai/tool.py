"""FOSS stub for ee.hogai.tool.MaxTool.

Used as a base class in 10+ products/*/backend/max_tools.py files (CDP,
surveys, error_tracking, replay, web_analytics, alerts, experiments,
feature_flags, tasks, user_interviews, workflows, revenue_analytics).
Subclasses are defined at module load (from ee.hogai.tool import MaxTool)
but never instantiated on FOSS since the Max AI agent loop never runs.
"""


class MaxTool:
    """No-op base class. Subclasses inherit the no-op."""

    name: str = ""
    description: str = ""
