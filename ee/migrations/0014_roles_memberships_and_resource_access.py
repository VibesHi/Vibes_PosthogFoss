"""FOSS stub: satisfies references like `('ee', '0014_roles_memberships_and_resource_access')`
declared by `products/conversations/backend/migrations/0013_ticket_assignment.py`.

No operations — Django just needs the node to exist in the migration graph so
posthog/products migrations that depend on it can be loaded. The actual schema
EE created here (Role, RoleMembership, OrganizationResourceAccess) was already
materialized by `0001_initial.py` to keep the table count flat in this fork.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("ee", "0001_initial")]
    operations: list = []
