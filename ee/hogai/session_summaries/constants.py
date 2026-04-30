"""FOSS stub for ee.hogai.session_summaries.constants.

These constants flow through Temporal workflows (the session-summary
sweep), which are imported at worker startup but never scheduled on FOSS
deploys. Values are upstream defaults to keep imports correct without
changing semantics if/when EE is restored.

The set of names exported here is the union of every
`from ee.hogai.session_summaries.constants import ...` reachable from
posthog/ + products/. If `bin/check-ee-stub-symbols` flags a new missing
name after an upstream sync, add it here.
"""

# Redis / DB caching
SESSION_SUMMARIES_DB_DATA_REDIS_TTL = 3600

# Models
SESSION_SUMMARIES_MODEL = "gpt-4o-mini"
DEFAULT_VIDEO_UNDERSTANDING_MODEL = "gemini-2.0-flash"

# Video export formats
MOMENT_VIDEO_EXPORT_FORMAT = "mp4"
FULL_VIDEO_EXPORT_FORMAT = "mp4"

# Asset retention
EXPIRES_AFTER_DAYS = 30

# Session candidate filtering
MIN_SESSION_DURATION_FOR_VIDEO_SUMMARY_S = 30
MIN_SESSION_DURATION_FOR_SUMMARY_MS = 30_000
MIN_ACTIVE_SECONDS_FOR_VIDEO_SUMMARY_S = 30
MAX_ACTIVE_SECONDS_FOR_VIDEO_SUMMARY_S = 1_800

# Workflow tuning
SESSION_GROUP_SUMMARIES_WORKFLOW_POLLING_INTERVAL_MS = 1_000

# Summary quality thresholds
FAILED_SESSION_SUMMARIES_MIN_RATIO = 0.5
FAILED_PATTERNS_EXTRACTION_MIN_RATIO = 0.5
FAILED_PATTERNS_ASSIGNMENT_MIN_RATIO = 0.5

# Pattern extraction tuning
PATTERNS_ASSIGNMENT_CHUNK_SIZE = 50
PATTERNS_EXTRACTION_MAX_TOKENS = 8_000
SINGLE_ENTITY_MAX_TOKENS = 4_000
