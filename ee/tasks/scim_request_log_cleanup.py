"""FOSS stub. SCIM is EE-only; cleanup task is a noop on FOSS."""


def cleanup_old_scim_request_logs(*args, **kwargs) -> None:
    return None


cleanup_old_scim_request_logs.delay = lambda *a, **kw: None  # type: ignore[attr-defined]
