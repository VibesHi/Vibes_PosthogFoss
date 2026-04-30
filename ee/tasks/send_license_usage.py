"""FOSS stub. License usage reporting is EE; no license on FOSS, no reporting."""


def send_license_usage(*args, **kwargs) -> None:
    return None


send_license_usage.delay = lambda *a, **kw: None  # type: ignore[attr-defined]
