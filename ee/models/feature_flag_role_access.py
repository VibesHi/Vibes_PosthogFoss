"""FOSS stub for ee.models.feature_flag_role_access.FeatureFlagRoleAccess.

Upstream: per-role feature flag editing access (EE RBAC). FOSS: empty.
"""

from django.db import models


class FeatureFlagRoleAccess(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_featureflagroleaccess"
