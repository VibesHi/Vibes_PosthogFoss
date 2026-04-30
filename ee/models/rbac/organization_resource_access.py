"""FOSS stub for ee.models.rbac.organization_resource_access."""

from django.db import models


class OrganizationResourceAccess(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_organizationresourceaccess"
