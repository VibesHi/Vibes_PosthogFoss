"""FOSS stub. Auto-rollback for feature flags is EE feature; noop on FOSS.
Manual rollback via UI still works."""


def check_flags_to_rollback(*args, **kwargs) -> None:
    return None


check_flags_to_rollback.delay = lambda *a, **kw: None  # type: ignore[attr-defined]
