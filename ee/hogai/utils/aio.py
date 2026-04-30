"""FOSS stub for ee.hogai.utils.aio.

Re-exports asgiref.sync.async_to_sync. Used in production paths
(products/dashboards/backend/api/dashboard.py, products/notebooks/), so
this needs to be a real working helper, not a no-op.
"""

from asgiref.sync import async_to_sync, sync_to_async

__all__ = ["async_to_sync", "sync_to_async"]
