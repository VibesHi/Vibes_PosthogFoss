"""Add `user`, `dashboard`, `level` columns to `ee_dashboardprivilege`.

Why: posthog.user_permissions.UserPermissions.dashboard_privileges does

    DashboardPrivilege.objects.filter(user=self.user).values_list(
        "dashboard_id", "level"
    )

This fires when a user opens a dashboard whose `restriction_level` is
`ONLY_COLLABORATORS_CAN_EDIT` and they aren't the owner/team-admin.
Without these fields, Django raises FieldError before SQL runs. With
them, the empty table returns [] and the caller falls back to the
default `PrivilegeLevel.CAN_VIEW`.

`dashboard` FK targets the `dashboards` app (label "dashboards"), which
houses the Dashboard model in PostHog's product-isolated layout.

Idempotent: nullable column add. Lock window is microseconds (table
empty on FOSS).

Existing PostHog deployments importing this fork: the real EE schema
already has these columns under the same names. Django's schema editor
checks introspection before issuing ADD COLUMN; if the column already
exists with compatible type, the operation is a no-op.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("ee", "0046_role_organization_and_user_related_name"),
        ("posthog", "0001_initial"),
        ("dashboards", "0001_migrate_dashboards_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="dashboardprivilege",
            name="user",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="ee_dashboard_privileges",
                to="posthog.user",
            ),
        ),
        migrations.AddField(
            model_name="dashboardprivilege",
            name="dashboard",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.CASCADE,
                related_name="ee_privileges",
                to="dashboards.dashboard",
            ),
        ),
        migrations.AddField(
            model_name="dashboardprivilege",
            name="level",
            field=models.PositiveSmallIntegerField(null=True),
        ),
    ]
