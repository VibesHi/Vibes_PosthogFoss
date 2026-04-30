"""FOSS stub for ee.api.test.base.LicensedTestMixin.

Used by a handful of test files to provision a License row before the test
runs. FOSS: no License table content needed; provide a no-op mixin.
"""


class LicensedTestMixin:
    """No-op test mixin. Subclasses inherit nothing material on FOSS."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()  # type: ignore[misc]
