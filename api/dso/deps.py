"""FastAPI dependencies of the DSO routes."""

from __future__ import annotations

from fastapi import Request

from api.dso.store import DsoStore
from core.errors.errors import ErrorException
from shared.custom_errors import errors


def get_store(request: Request) -> DsoStore:
    """The store `main.py`'s lifespan loaded, or 503 DATA_UNAVAILABLE.

    In a running container the store is always there: a data directory that fails
    its checks fails the boot. The 503 covers an app started without its lifespan
    (a bare ASGI transport in a test, say), so it refuses instead of answering
    from nothing.
    """
    store = getattr(request.app.state, "store", None)
    if not isinstance(store, DsoStore):
        raise ErrorException(errors.dso.DATA_UNAVAILABLE, status_code=503)
    return store
