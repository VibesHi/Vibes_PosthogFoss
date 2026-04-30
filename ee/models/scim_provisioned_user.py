"""FOSS stub for ee.models.scim_provisioned_user.SCIMProvisionedUser.

Upstream: tracks users created/synced via SCIM IdP. FOSS: no SCIM, empty.
"""

from django.db import models


class SCIMProvisionedUser(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_scimprovisioneduser"
