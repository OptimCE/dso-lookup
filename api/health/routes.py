"""Liveness and readiness.

`/health/readiness` is billing's shape (`{"status", "checks"}`, 200 or 503). The
spec's `/healthz` is kept as an alias of it, so both names work; the compose
healthchecks (dev and production) probe `/health/readiness`.

Ready means the data is there AND is the data it says it is:
- the store is loaded (a directory that fails its checks fails the boot, so a
  running process without one was started without its lifespan);
- the counts recounted from the files equal `release.json`'s `counts` (the
  mini fixture has 6 communes: the expected numbers come from the data itself);
- the version served is `release.json`'s version;
- AND, only for a release (no `-draft`) that is not a fixture
  (`DSO_DATA_FIXTURE`, refused in production): the national counts, 565
  communes = 261 WAL / 285 VLA / 19 BRU (REFNIS 2025). A wrong-but-consistent
  release then stays unready instead of quietly answering for part of Belgium.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.dso.store import DsoStore
from core import metrics as app_metrics
from core.config import settings

health_router = APIRouter()
healthz_router = APIRouter()

# REFNIS 2025 (belgian-dso-map validation rule 1). Next change: the municipal
# mergers of 01/01/2031.
NATIONAL_COMMUNES = 565
NATIONAL_COMMUNES_BY_REGION = {"BRU": 19, "VLA": 285, "WAL": 261}


def check_data(store: object, *, fixture: bool) -> dict[str, Any]:
    """The readiness verdict on a store (pure, so the tests can feed it anything)."""
    if not isinstance(store, DsoStore):
        return {"ok": False, "error": "data not loaded"}
    problems: list[str] = []
    if store.counts != store.release.counts:
        problems.append("counts differ from release.json")
    if store.index.data_version != store.release.version:
        problems.append("served version differs from release.json")
    national = not store.is_draft and not fixture
    if national and (
        store.counts["communes"] != NATIONAL_COMMUNES
        or store.counts["communes_by_region"] != NATIONAL_COMMUNES_BY_REGION
    ):
        problems.append(
            f"a release must hold {NATIONAL_COMMUNES} communes "
            f"({NATIONAL_COMMUNES_BY_REGION}), this one holds {store.counts['communes']} "
            f"({store.counts['communes_by_region']})"
        )
    result: dict[str, Any] = {
        "ok": not problems,
        "data_version": store.release.version,
        "communes": store.counts["communes"],
        "national_counts_checked": national,
    }
    if problems:
        result["error"] = "; ".join(problems)
    return result


@health_router.get("/liveness", tags=["Health"])
async def liveness():
    return {"status": "alive"}


@health_router.get("/readiness", tags=["Health"])
async def readiness(request: Request) -> JSONResponse:
    checks = {
        "data": check_data(
            getattr(request.app.state, "store", None), fixture=settings.DSO_DATA_FIXTURE
        ),
    }

    # Emit one counter point per component per probe so the metrics backend
    # can plot the failure rate (rate(health_checks_total{ok="false"}[5m]))
    # and alert on it without parsing log lines.
    for component, result in checks.items():
        app_metrics.health_checks.add(
            1,
            {"component": component, "ok": "true" if result["ok"] else "false"},
        )

    is_ready = all(result["ok"] for result in checks.values())

    payload = {
        "status": "healthy" if is_ready else "unhealthy",
        "checks": checks,
    }

    return JSONResponse(
        status_code=200 if is_ready else 503,
        content=payload,
    )


@healthz_router.get("/healthz", tags=["Health"])
async def healthz(request: Request) -> JSONResponse:
    """The spec's name for readiness."""
    return await readiness(request)
