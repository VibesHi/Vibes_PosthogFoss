"""One-shot backfill: populate `posthog_organization.available_product_features`
on every existing org.

The fork's License stub now returns a synthetic enterprise license, so
`Organization.update_available_product_features()` will populate the field
correctly going forward -- on every signup (via the `pre_save` signal at
`posthog/models/organization.py:523`) AND on every hourly periodic task
(`posthog.tasks.scheduled.py:378`). But existing orgs created BEFORE this
migration ran with a None-returning `first_valid()`, so their
`available_product_features` is empty -- and the next hourly sync would
fix them, but the operator probably wants the unlock immediately (they're
running this migration *because* a gate is blocking them).

Calls the runtime `update_available_product_features()` (not the historical
model returned by `apps.get_model`) because we need the model's behavior,
not just its schema. This is safe because:
  * The method only mutates `available_product_features` (the field exists
    in this migration version of the schema).
  * It calls `License.objects.first_valid()` which is OUR stub method,
    NOT a DB query against historical Licence schema.
  * We don't run other side-effecting model methods here.

Reverse: clears `available_product_features` to NULL. Reversible without
data loss because the next periodic sync will re-populate it.
"""

from django.db import migrations


def unlock_features_for_existing_orgs(apps, schema_editor) -> None:
    # Use runtime model intentionally -- see module docstring.
    from posthog.models.organization import Organization

    for org in Organization.objects.all():
        org.update_available_product_features()
        org.save(update_fields=["available_product_features"])


def relock_features_for_existing_orgs(apps, schema_editor) -> None:
    # Use historical model for the reverse path; we only touch the field,
    # not any methods.
    Organization = apps.get_model("posthog", "Organization")
    Organization.objects.update(available_product_features=None)


class Migration(migrations.Migration):
    dependencies = [
        ("ee", "0043_rolemembership_user_organization_member"),
    ]

    operations = [
        migrations.RunPython(
            unlock_features_for_existing_orgs,
            reverse_code=relock_features_for_existing_orgs,
        ),
    ]
