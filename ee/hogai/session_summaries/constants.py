"""FOSS stub for ee.hogai.session_summaries.constants.

These constants flow through Temporal workflows (the session-summary
sweep), which are imported at worker startup but never scheduled on FOSS
deploys. Values are upstream defaults to keep imports correct without
changing semantics if/when EE is restored.
"""

SESSION_SUMMARIES_DB_DATA_REDIS_TTL = 3600
SESSION_SUMMARIES_MODEL = "gpt-4o-mini"
DEFAULT_VIDEO_UNDERSTANDING_MODEL = "gemini-2.0-flash"
MOMENT_VIDEO_EXPORT_FORMAT = "mp4"
FULL_VIDEO_EXPORT_FORMAT = "mp4"
