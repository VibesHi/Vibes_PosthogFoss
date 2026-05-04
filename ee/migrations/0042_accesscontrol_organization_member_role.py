"""Add `organization_member` and `role` FK columns to `ee_accesscontrol`.

Why: `posthog.models.team.team.all_users_with_access()` (called from signup
-> create_team -> sync_user_product_lists_for_new_team Celery task) does:

    AccessControl.objects.filter(
        team_id=...,
        resource="project",
        resource_id=...,
        organization_member=None,
        role=None,
        access_level="none",
    ).exists()

Without these columns Django raises FieldError before any SQL runs, breaking
the entire signup flow. Both columns stay NULL on FOSS (no UI to insert
AccessControl rows), so the .exists() always returns False -> the team is
treated as not-private -> all org members get access. Matches the spirit
of EE_AVAILABLE=False.

Idempotent: adds nullable columns. Safe on busy tables (small lock window).
The table is empty in practice on FOSS deploys, so the lock is microseconds.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ee", "0041_migrate_dashboards_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="accesscontrol",
            name="organization_member",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="ee_access_controls",
                to="posthog.organizationmembership",
            ),
        ),
        migrations.AddField(
            model_name="accesscontrol",
            name="role",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="ee_access_controls",
                to="ee.role",
            ),
        ),
    ]
