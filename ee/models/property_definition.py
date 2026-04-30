"""FOSS stub for ee.models.property_definition.EnterprisePropertyDefinition.

Same pattern as EnterpriseEventDefinition.
"""

from django.db import models


class EnterprisePropertyDefinition(models.Model):
    class Meta:
        app_label = "ee"
        db_table = "ee_enterprisepropertydefinition"
