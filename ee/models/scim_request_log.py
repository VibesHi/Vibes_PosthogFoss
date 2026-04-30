"""FOSS stub for ee.models.scim_request_log.SCIMRequestLog.

Upstream: audit log for SCIM API calls. FOSS: empty.
"""

from django.db import models


class SCIMRequestLog(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_scimrequestlog"
