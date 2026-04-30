"""Shared helpers for ee/ stubs. Avoids duplicating the same
queryset/manager-shaped no-op across 15 model stubs.
"""

from typing import Any


class _StubQuerySet:
    """Empty queryset that supports the most common chained operations."""

    def __iter__(self):
        return iter([])

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def __getitem__(self, item):
        raise IndexError("stub queryset")

    def filter(self, *args, **kwargs) -> "_StubQuerySet":
        return self

    def exclude(self, *args, **kwargs) -> "_StubQuerySet":
        return self

    def all(self) -> "_StubQuerySet":
        return self

    def order_by(self, *args, **kwargs) -> "_StubQuerySet":
        return self

    def values(self, *args, **kwargs) -> "_StubQuerySet":
        return self

    def values_list(self, *args, **kwargs) -> "_StubQuerySet":
        return self

    def select_related(self, *args, **kwargs) -> "_StubQuerySet":
        return self

    def prefetch_related(self, *args, **kwargs) -> "_StubQuerySet":
        return self

    def annotate(self, *args, **kwargs) -> "_StubQuerySet":
        return self

    def first(self) -> None:
        return None

    def last(self) -> None:
        return None

    def exists(self) -> bool:
        return False

    def count(self) -> int:
        return 0

    def get(self, *args, **kwargs) -> Any:
        raise _StubDoesNotExist("stub model: no rows")

    def update(self, *args, **kwargs) -> int:
        return 0

    def delete(self) -> tuple[int, dict]:
        return 0, {}


class _StubDoesNotExist(Exception):
    pass


class StubManager:
    """Mimics Django's `Model.objects` enough for read-only, no-row paths."""

    def __init__(self, model_cls=None):
        self.model = model_cls

    def _qs(self) -> _StubQuerySet:
        return _StubQuerySet()

    def filter(self, *a, **kw) -> _StubQuerySet:
        return self._qs()

    def all(self) -> _StubQuerySet:
        return self._qs()

    def first(self) -> None:
        return None

    def last(self) -> None:
        return None

    def exists(self) -> bool:
        return False

    def count(self) -> int:
        return 0

    def get(self, *a, **kw):
        raise _StubDoesNotExist("stub model: no rows")

    def get_or_create(self, *a, **kw):
        raise NotImplementedError("EE feature unavailable in FOSS fork")

    def create(self, *a, **kw):
        raise NotImplementedError("EE feature unavailable in FOSS fork")

    def values(self, *a, **kw) -> _StubQuerySet:
        return self._qs()

    def values_list(self, *a, **kw) -> _StubQuerySet:
        return self._qs()

    def order_by(self, *a, **kw) -> _StubQuerySet:
        return self._qs()


def stub_function(*args, **kwargs) -> None:
    """Generic no-op for stubbed functions. Returns None."""
    return None


class _PermissiveConstantsMeta(type):
    """Metaclass that returns a string sentinel for any missing class attr.

    Real upstream has these as `Enum` subclasses with a fixed set of members
    (e.g. AssistantMode.ASSISTANT, AssistantNodeName.ROOT). On FOSS we don't
    know the full member set without diffing upstream, AND it churns. So:

      class AssistantMode(metaclass=_PermissiveConstantsMeta):
          ASSISTANT = "assistant"  # explicit when we know the member

    Then:
      - `AssistantMode.ASSISTANT` → "assistant"  (explicit class attr wins)
      - `AssistantMode.WHATEVER`  → "whatever"   (metaclass __getattr__)

    `__getattr__` only fires when normal attribute lookup fails, so explicit
    class attributes are returned as-is. The fallback covers upstream-drift.

    Equality (`mode == AssistantMode.ASSISTANT`) is plain string comparison.
    Iteration (`for m in AssistantMode`) does NOT work — add explicit members
    or convert to a real Enum if you hit that path.
    """

    def __getattr__(cls, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        return name.lower()


class PermissiveConstants(metaclass=_PermissiveConstantsMeta):
    """Inherit to get permissive `.SOME_NEW_MEMBER` access on FOSS stubs."""

    pass
