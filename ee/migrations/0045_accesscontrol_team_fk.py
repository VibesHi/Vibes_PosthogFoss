"""Promote `AccessControl.team_id` from IntegerField to a real ForeignKey.

Why: `posthog.rbac.user_access_control.UserAccessControl` is exercised on
every authenticated request once `available_product_features` includes
`advanced_permissions` -- which is now the default on FOSS thanks to the
synthetic license in `ee.models.license`. Two patterns OSS code uses:

    AccessControl.objects.filter(team__organization_id=...)   # FK traversal
    ac.team.organization_id                                   # descriptor access

Both require `team` to be a real ForeignKey, not a bare IntegerField.

Strategy: state-only operation. Existing `team_id` column stays put as
INT4 (no ALTER TABLE, no FK constraint). The new field reuses the column
via `db_column="team_id"`. `db_constraint=False` skips the cross-type FK
constraint that would otherwise fail (ee_accesscontrol.team_id is INT4,
posthog_team.id is BIGINT). Empty table on FOSS, so the relaxed
constraint is moot.

Existing PostHog deployments importing this fork on top of real EE data:
the column type may already be BIGINT and a real FK constraint may exist
-- both fine, since we only mutate Django state, not the DB. The model
will happily query against BIGINT columns.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ee", "0044_unlock_features_for_existing_orgs"),
        ("posthog", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(
                    model_name="accesscontrol",
                    name="team_id",
                ),
                migrations.AddField(
                    model_name="accesscontrol",
                    name="team",
                    field=models.ForeignKey(
                        null=True,
                        on_delete=models.deletion.CASCADE,
                        related_name="ee_access_controls",
                        to="posthog.team",
                        db_column="team_id",
                        db_constraint=False,
                    ),
                ),
            ],
            database_operations=[],
        ),
    ]
