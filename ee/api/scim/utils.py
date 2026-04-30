"""FOSS stub for ee.api.scim.utils.

posthog/api/organization_domain.py imports several utility names from here
to wire SCIM provisioning into the OrganizationDomain admin. SCIM is an EE
feature gated by license and disabled here; these stubs return defaults
that mean "SCIM not active, fall through to normal flows".
"""

from typing import Any


def is_scim_enabled(*args, **kwargs) -> bool:
    return False


def is_scim_request(*args, **kwargs) -> bool:
    return False


def scim_authenticated(*args, **kwargs) -> bool:
    return False


def get_scim_user_for_request(*args, **kwargs) -> Any:
    return None


def log_scim_request(*args, **kwargs) -> None:
    return None
