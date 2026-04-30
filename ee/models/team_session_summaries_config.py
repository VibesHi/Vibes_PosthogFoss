"""FOSS stub for ee.models.team_session_summaries_config.

Upstream: per-team config for session summary AI feature. FOSS: empty.
"""

from django.db import models


class TeamSessionSummariesConfig(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_teamsessionsummariesconfig"
