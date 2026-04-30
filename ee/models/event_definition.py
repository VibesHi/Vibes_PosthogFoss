"""FOSS stub for ee.models.event_definition.EnterpriseEventDefinition.

Upstream: extends OSS EventDefinition with description/owner/verified-by
fields shown in the EE Data Management UI. FOSS: empty extension table.

posthog/api/event_definition.py imports this only inside `if EE_AVAILABLE:`
branches; with EE_AVAILABLE=False, no instance methods on it ever run, but
the import must still resolve at module load time.
"""

from django.db import models


class EnterpriseEventDefinition(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_enterpriseeventdefinition"
