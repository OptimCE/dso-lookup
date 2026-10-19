"""Liveness, readiness and the /healthz alias."""

import dataclasses

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from api.dso.store import DsoStore
from api.health.routes import NATIONAL_COMMUNES, NATIONAL_COMMUNES_BY_REGION, check_data
from core.config import Environment, Settings
from main import app


async def test_liveness(client: AsyncClient):
    response = await client.get("/health/liveness")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness_is_green_on_the_shipped_data(client: AsyncClient, store: DsoStore):
    response = await client.get("/health/readiness")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "healthy"
    data = body["checks"]["data"]
    assert data["ok"] is True
    assert data["data_version"] == store.release.version
    assert data["communes"] == store.release.counts["communes"]
    # A draft never asserts the national counts.
    assert data["national_counts_checked"] is (not store.is_draft)


async def test_healthz_is_readiness(client: AsyncClient):
    readiness = await client.get("/health/readiness")
    healthz = await client.get("/healthz")
    assert healthz.status_code == readiness.status_code
    assert healthz.json() == readiness.json()


async def test_unready_without_data():
    """Started without its lifespan (so without data): 503, and the lookups refuse."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as bare:
        app.state.store = None
        for path in ("/health/readiness", "/healthz"):
            response = await bare.get(path)
            assert response.status_code == 503
            assert response.json()["checks"]["data"] == {"ok": False, "error": "data not loaded"}
        lookup = await bare.get("/dso/resolve", params={"postcode": "1000"})
        assert lookup.status_code == 503
        assert lookup.json()["error_code"] == 2600
        assert (await bare.get("/health/liveness")).status_code == 200


def _as_release(store: DsoStore, *, communes: int, by_region: dict[str, int]) -> DsoStore:
    """The store, relabelled as a non-draft release with the given counts."""
    counts = {**store.counts, "communes": communes, "communes_by_region": by_region}
    version = store.release.version.removesuffix("-draft")
    release = dataclasses.replace(store.release, version=version, counts=counts)
    index = dataclasses.replace(store.index, data_version=version)
    return dataclasses.replace(store, release=release, index=index, counts=counts)


def test_a_release_must_be_national(store: DsoStore):
    small = _as_release(
        store,
        communes=store.counts["communes"],
        by_region=store.counts["communes_by_region"],
    )
    verdict = check_data(small, fixture=False)
    assert verdict["ok"] is (small.counts["communes"] == NATIONAL_COMMUNES)
    assert verdict["national_counts_checked"] is True
    # ... unless it is a fixture served on purpose (never in production).
    assert check_data(small, fixture=True)["ok"] is True


def test_the_national_counts_pass(store: DsoStore):
    national = _as_release(store, communes=565, by_region=dict(NATIONAL_COMMUNES_BY_REGION))
    assert check_data(national, fixture=False)["ok"] is True
    wrong_split = _as_release(store, communes=565, by_region={"BRU": 19, "VLA": 284, "WAL": 262})
    assert check_data(wrong_split, fixture=False)["ok"] is False


def test_counts_that_differ_from_release_json_are_unready(store: DsoStore):
    counts = {**store.counts, "rows": store.counts["rows"] + 1}
    verdict = check_data(dataclasses.replace(store, counts=counts), fixture=True)
    assert verdict["ok"] is False
    assert "counts differ" in verdict["error"]


def test_a_version_that_differs_is_unready(store: DsoStore):
    index = dataclasses.replace(store.index, data_version="2020.01.0")
    verdict = check_data(dataclasses.replace(store, index=index), fixture=True)
    assert verdict["ok"] is False


def test_the_fixture_switch_is_refused_in_production():
    with pytest.raises(ValidationError, match="DSO_DATA_FIXTURE"):
        Settings(
            ENV=Environment.PRODUCTION,
            LOGGING_TOKEN="t",
            LOGGING_LOGS_URL="http://collector/v1/logs",
            LOGGING_METRICS_URL="http://collector/v1/metrics",
            DSO_DATA_FIXTURE=True,
        )


def test_production_requires_the_logging_settings():
    with pytest.raises(ValidationError, match="LOGGING_TOKEN"):
        Settings(ENV=Environment.PRODUCTION, LOGGING_TOKEN="")
