"""DSO lookup HTTP routes.

Internal API: crm-backend is the only caller, over the Docker network, through
`src/modules/dso/infra/dso-lookup.client.ts`. No gateway, no authentication,
no KrakenD route, no CORS. Every body is `{"data": <payload>, "error_code": 0}`.

Contract rules that show here:
- A malformed postcode, NIS code or name is an ANSWER (`not_found`, or the
  filter is ignored), never a 422: the query parameters are plain optional
  strings, and the ported lookup decides. The release's vectors.csv runs through
  these routes (tests/test_vectors.py).
- Nothing here logs a parameter. The one trace of a lookup is the
  `dso.lookups.total{endpoint,status}` counter; uvicorn runs with
  `--no-access-log` (both Dockerfiles), because its access line would carry the
  query string.

NOTE: this module intentionally does NOT `from __future__ import annotations`.
The ``with_default_error`` wrapper resolves string annotations against its own
module globals, so stringified dependency types would be invisible to FastAPI.
"""

from fastapi import APIRouter, Depends

from api.dso import lookup
from api.dso.deps import get_store
from api.dso.ean import ean_check_out, ean_status
from api.dso.schemas import (
    EanCheckOut,
    MetaOut,
    OperatorOut,
    ResolveOut,
    meta_out,
    operator_out,
    resolve_out,
)
from api.dso.store import DsoStore
from core import metrics as app_metrics
from core.api_response import ApiResponse
from core.errors.with_default_error import with_default_error
from shared.custom_errors import errors

dso_routes = APIRouter(prefix="/dso")


@dso_routes.get("/resolve")
@with_default_error(errors.dso.RESOLVE)
async def resolve(
    postcode: str | None = None,
    ins: str | None = None,
    section: str | None = None,
    store: DsoStore = Depends(get_store),
) -> ApiResponse[ResolveOut]:
    """By postcode, optionally narrowed by commune (`ins`) then section."""
    found = lookup.resolve(store.index, postcode, ins, section)
    app_metrics.count_lookup("resolve", found["status"])
    return ApiResponse(data=resolve_out(store, found))


@dso_routes.get("/resolve-address")
@with_default_error(errors.dso.RESOLVE_ADDRESS)
async def resolve_address(
    nis_code: str | None = None,
    postcode: str | None = None,
    post_name: str | None = None,
    store: DsoStore = Depends(get_store),
) -> ApiResponse[ResolveOut]:
    """By the address the BeSt picker returned: NIS code, postcode, postal name.

    The NIS code goes through as sent: the lookup TRIMs it and compares it exactly.
    Every INS is `[1-9][0-9]{4}` (contract), so its int form is the same string,
    and no zero-padding could ever turn another value into a match."""
    found = lookup.resolve_address(store.index, nis_code, postcode, post_name)
    app_metrics.count_lookup("resolve_address", found["status"])
    return ApiResponse(data=resolve_out(store, found))


@dso_routes.get("/operators")
@with_default_error(errors.dso.OPERATORS)
async def operators(
    store: DsoStore = Depends(get_store),
) -> ApiResponse[list[OperatorOut]]:
    """The catalogue in display order: the DSOs in the app's GRD_OPTIONS order,
    then Enexis (foreign gas), then Elia (local transmission, `lookup:false`) last."""
    return ApiResponse(data=[operator_out(operator) for operator in store.operators])


# `{ean:path}` so that an EMPTY segment (`/dso/ean//check`) is still a request
# this route answers ("format_invalid"), as the contract's `ean-empty` vector says.
@dso_routes.get("/ean/{ean:path}/check")
@with_default_error(errors.dso.EAN_CHECK)
async def ean_check(ean: str) -> ApiResponse[EanCheckOut]:
    """Informative EAN check; needs no data, so it answers even without the store."""
    check = ean_check_out(ean)
    app_metrics.count_lookup("ean_check", ean_status(check))
    return ApiResponse(data=check)


@dso_routes.get("/meta")
@with_default_error(errors.dso.META)
async def meta(store: DsoStore = Depends(get_store)) -> ApiResponse[MetaOut]:
    """Which data release is served (crm-backend logs it once at boot)."""
    return ApiResponse(data=meta_out(store))
