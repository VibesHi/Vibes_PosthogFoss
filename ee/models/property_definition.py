"""FOSS shim for ee.models.property_definition.

Honors upstream's "EE not installed -> ImportError" contract instead of
exposing an empty Django model stub.

Why this matters (the bug we hit before this shape):

  posthog/api/event.py:_is_property_hidden does:

      try:
          from ee.models.property_definition import EnterprisePropertyDefinition
          property_is_hidden = EnterprisePropertyDefinition.objects.filter(
              team=..., name=..., type=..., hidden=True,
          ).exists()
      except ImportError:
          property_is_hidden = False

  This is the ONLY ungated import of EnterprisePropertyDefinition in
  upstream code -- every other site is inside `if EE_AVAILABLE:`. The
  upstream design assumes a missing EE package will raise ImportError so
  the safety net runs.

  When this file used to expose a `class EnterprisePropertyDefinition(models.Model)`
  stub with only `id`, the import succeeded but `.filter(hidden=True)` blew
  up with `FieldError: Cannot resolve keyword 'hidden' into field`. Every
  request to `/api/event/values?key=...` from the property-value picker
  500'd, surfacing as a "Failed to load property values" toast in the UI.

How this works:

  Module import succeeds (so `import ee.models.property_definition` and
  the smoke probe in bin/foss_smoke_test.py both pass) but any attribute
  access for the missing symbol raises ImportError. PEP 562 module
  __getattr__ runs on `from ... import EnterprisePropertyDefinition`
  because Python falls back to it when the name is not in the module's
  namespace.

  Cost vs alternatives:
    - Dummy model + DB migration to create an empty `ee_enterprisepropertydefinition`
      table: requires a migration, ships a vestigial table forever, lies
      about feature availability (the model exists, just no rows).
    - Custom Manager that short-circuits .filter(): brittle, every new
      EE codepath that learns to use this model would silently no-op.
    - This shim: matches the upstream contract exactly, fails loudly if
      anyone reaches past `try/except ImportError`, no schema footprint.

Note: `ee/models/__init__.py` deliberately does NOT re-export this symbol
(removing the line would otherwise propagate the ImportError to every
`from ee.models import X` site at module-load time). Any code that needs
to test for EE-property availability should import from this submodule
inside a try/except, never from `ee.models`.
"""


def __getattr__(name: str):
    if name == "EnterprisePropertyDefinition":
        raise ImportError(
            "EnterprisePropertyDefinition is not available on this FOSS fork. "
            "If you reached this without a try/except ImportError guard, the "
            "upstream code path is missing one — wrap the import."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
