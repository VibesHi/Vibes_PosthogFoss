"""FOSS stub for ee.models.session_summaries.

Upstream: persists AI-generated session replay summaries. FOSS: AI features
removed, but the imports must resolve. The two Django models below are
referenced in posthog/temporal/session_replay/session_summary/* (Temporal
workflows) which never run on a FOSS deploy. The two non-model classes are
dataclass-shaped containers consumed during summary generation.
"""

from dataclasses import dataclass, field
from typing import Any

from django.db import models


class SessionGroupSummary(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_sessiongroupsummary"


class SingleSessionSummary(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_singlesessionsummary"


@dataclass
class ExtraSummaryContext:
    """Container for prompt-time context. FOSS: never populated."""

    user_role: str | None = None
    custom_instructions: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionSummaryRunMeta:
    """Metadata about a summary generation run. FOSS: unused."""

    run_id: str | None = None
    started_at: Any = None
    finished_at: Any = None
    model: str | None = None
