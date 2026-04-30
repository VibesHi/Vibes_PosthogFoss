"""FOSS stub. CoreMemory is Max AI's persistent context store.

Reachable from OSS via posthog/hogql_queries/ai/suggested_questions_query_runner.py
(registered in the OSS query runner registry). Backing table is empty on FOSS so
`CoreMemory.objects.get(team=team)` raises `DoesNotExist` (caught upstream) and
the runner returns `core_memory = None`, which it handles gracefully.

Schema kept minimal — fields here must stay in sync with ee/migrations/0001_initial.py.
"""

from django.db import models


class CoreMemory(models.Model):
    team_id = models.IntegerField(null=True, db_index=True)
    text = models.TextField(blank=True, default="")
    formatted_text = models.TextField(blank=True, default="")

    class Meta:
        app_label = "ee"
        db_table = "ee_corememory"
