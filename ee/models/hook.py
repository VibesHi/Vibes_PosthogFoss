"""FOSS stub. Hook model used by posthog/management/commands/migrate_hooks.py
(only on explicit invocation).

Schema kept minimal — fields here must stay in sync with ee/migrations/0001_initial.py.
"""

from django.db import models


class Hook(models.Model):
    team_id = models.IntegerField(null=True, db_index=True)
    event = models.CharField(max_length=200, null=True)
    target = models.URLField(blank=True, null=True)

    class Meta:
        app_label = "ee"
        db_table = "ee_hook"
