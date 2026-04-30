"""FOSS stub initial migration for the `ee` app.

Creates minimal tables (id PK only, plus a few defensive fields) for every
model defined in ee/models/. Required because:

  - 9 migrations across posthog/ and products/ have FK references like
    `to="ee.role"` and `to="ee.conversation"`. Django can't resolve those
    unless the `ee` app is registered with at least the target models.
  - posthog/cloud_utils.py and posthog/utils.py import License and run
    `License.objects.filter(...)`. Without a backing table, those queries
    raise ProgrammingError. With this empty table, they return no rows.
  - posthog/hogql_queries/ai/suggested_questions_query_runner.py is
    registered in the OSS query runner registry and queries `CoreMemory`.
    Without a backing table, the call raises ProgrammingError instead of
    the expected DoesNotExist, which the runner can't recover from.

Existing PostHog deployments (with the *real* upstream ee_role, ee_license,
etc. tables already populated):

    python manage.py migrate ee 0001 --fake

This marks the migration as applied without trying to CREATE TABLE on
already-existing tables. Field-level mismatches between this stub schema
and the real upstream schema are accepted -- OSS code never references the
extra columns since EE_AVAILABLE=False keeps EE-gated branches dead.

Fresh installs run this migration normally.
"""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies: list = []

    operations = [
        # Role.id MUST be UUID (not BigAutoField) — real upstream EE has
        # UUIDField here, and posthog/migrations/0717_*, 0829_*, 1117_* hardcode
        # raw SQL like `REFERENCES "ee_role"("id")` with `role_id uuid NULL`.
        # Postgres rejects FK from uuid → bigint with "incompatible types".
        # Same applies to Conversation below — products/signals migration FKs
        # to ee.conversation, and Django generates the FK column type from
        # ee_conversation.id's declared type.
        migrations.CreateModel(
            name="Role",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=200)),
            ],
            options={"db_table": "ee_role"},
        ),
        migrations.CreateModel(
            name="RoleMembership",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                (
                    "role",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="role_memberships",
                        to="ee.role",
                    ),
                ),
            ],
            options={"db_table": "ee_rolemembership"},
        ),
        migrations.CreateModel(
            name="AccessControl",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("team_id", models.IntegerField(null=True)),
                ("resource", models.CharField(max_length=200, null=True)),
                ("resource_id", models.CharField(max_length=200, null=True)),
                ("access_level", models.CharField(max_length=200, null=True)),
            ],
            options={"db_table": "ee_accesscontrol"},
        ),
        migrations.CreateModel(
            name="OrganizationResourceAccess",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_organizationresourceaccess"},
        ),
        migrations.CreateModel(
            name="Conversation",
            fields=[
                # See Role above for why this is UUID, not BigAutoField.
                ("id", models.UUIDField(default=uuid.uuid4, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=200, null=True)),
            ],
            options={"db_table": "ee_conversation"},
        ),
        migrations.CreateModel(
            name="License",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("key", models.CharField(blank=True, max_length=400)),
                ("plan", models.CharField(blank=True, max_length=200, null=True)),
                ("valid_until", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "ee_license"},
        ),
        migrations.CreateModel(
            name="DashboardPrivilege",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_dashboardprivilege"},
        ),
        migrations.CreateModel(
            name="ExplicitTeamMembership",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_explicitteammembership"},
        ),
        migrations.CreateModel(
            name="FeatureFlagRoleAccess",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_featureflagroleaccess"},
        ),
        migrations.CreateModel(
            name="SCIMProvisionedUser",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_scimprovisioneduser"},
        ),
        migrations.CreateModel(
            name="SCIMRequestLog",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_scimrequestlog"},
        ),
        migrations.CreateModel(
            name="EnterpriseEventDefinition",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_enterpriseeventdefinition"},
        ),
        migrations.CreateModel(
            name="EnterprisePropertyDefinition",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_enterprisepropertydefinition"},
        ),
        migrations.CreateModel(
            name="SessionGroupSummary",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_sessiongroupsummary"},
        ),
        migrations.CreateModel(
            name="SingleSessionSummary",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_singlesessionsummary"},
        ),
        migrations.CreateModel(
            name="TeamSessionSummariesConfig",
            fields=[("id", models.BigAutoField(primary_key=True, serialize=False))],
            options={"db_table": "ee_teamsessionsummariesconfig"},
        ),
        # CoreMemory is reachable from OSS code: SuggestedQuestionsQueryRunner
        # (registered in posthog/hogql_queries/query_runner.py) does
        # `CoreMemory.objects.get(team=...)` and the `except CoreMemory.DoesNotExist`
        # there does NOT catch `ProgrammingError: relation does not exist`. So we
        # need a real (empty) table — empty rows make `core_memory` return None,
        # which the runner handles gracefully.
        migrations.CreateModel(
            name="CoreMemory",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("team_id", models.IntegerField(null=True, db_index=True)),
                ("text", models.TextField(blank=True, default="")),
                ("formatted_text", models.TextField(blank=True, default="")),
            ],
            options={"db_table": "ee_corememory"},
        ),
        # Hook is only touched by `manage.py migrate_hooks` (one-shot command).
        # Adding the table preemptively so the command doesn't crash if invoked.
        migrations.CreateModel(
            name="Hook",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("team_id", models.IntegerField(null=True, db_index=True)),
                ("event", models.CharField(max_length=200, null=True)),
                ("target", models.URLField(blank=True, null=True)),
            ],
            options={"db_table": "ee_hook"},
        ),
    ]
