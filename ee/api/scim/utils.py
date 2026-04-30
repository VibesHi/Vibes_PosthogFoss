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


def disable_scim_for_domain(*args, **kwargs) -> None:
    """Upstream clears SCIM token + integration on an OrganizationDomain. FOSS no-op."""
    return None


def enable_scim_for_domain(*args, **kwargs) -> None:
    """Upstream provisions a SCIM token for an OrganizationDomain. FOSS no-op."""
    return None


def regenerate_scim_token(*args, **kwargs) -> str:
    """Upstream rotates and returns a new SCIM bearer token. FOSS: empty string."""
    return ""


def get_scim_base_url(*args, **kwargs) -> str:
    """Upstream returns the SCIM endpoint URL for a domain. FOSS: empty string."""
    return ""


def mask_email(value: str | None, *args, **kwargs) -> str:
    """Upstream returns a redacted form for SCIM audit logs. FOSS: pass through (never logged)."""
    return value or ""


def mask_string(value: str | None, *args, **kwargs) -> str:
    return value or ""
