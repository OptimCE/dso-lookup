"""Tests for core/metrics.py instruments.

We attach an ``InMemoryMetricReader`` to a freshly-built ``MeterProvider``
for the duration of each test, re-fetch a meter, and create local mirror
instruments. The mirror is needed because the module-level instruments
in ``core.metrics`` are bound to whatever provider was current at import
time (the OTel ``_ProxyMeterProvider`` in test environments without
``setup_tracer_provider``); the proxy is supposed to rebind, but the
test fixture stays portable across SDK versions by mirroring locally
and asserting on the in-memory reader directly.

For ``dso.lookups.total`` the routes go through ``count_lookup``, which is
patched here to record its labels: the counter's labels are the only trace a
lookup leaves, so they must stay inside their closed vocabularies.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from core import metrics as app_metrics


def _collect(reader: InMemoryMetricReader) -> dict[str, list]:
    """Return a flat ``{metric_name: [data_points]}`` mapping for assertions."""
    data = reader.get_metrics_data()
    found: dict[str, list] = {}
    if data is None:
        return found
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                # Counter / Sum and Histogram both expose .data.data_points
                found.setdefault(metric.name, []).extend(metric.data.data_points)
    return found


def test_counter_records_into_in_memory_reader():
    """End-to-end check that a Counter bound to a freshly-installed
    MeterProvider exports data points via InMemoryMetricReader. This
    protects against future API/SDK upgrades silently breaking the
    OTel-API-to-SDK proxy binding.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    previous_provider = metrics.get_meter_provider()
    metrics.set_meter_provider(provider)
    try:
        meter = metrics.get_meter("test")
        counter = meter.create_counter("test.counter")
        counter.add(3, {"x": "y"})

        collected = _collect(reader)
        assert "test.counter" in collected
        points = collected["test.counter"]
        assert sum(p.value for p in points) == 3
        assert any(p.attributes.get("x") == "y" for p in points)
    finally:
        metrics.set_meter_provider(previous_provider)


def test_the_instruments_are_named_as_the_dashboards_expect():
    assert getattr(app_metrics.lookups, "_name", None) == "dso.lookups.total"
    assert getattr(app_metrics.health_checks, "_name", None) == "health.checks.total"


@pytest.fixture
def lookup_labels(monkeypatch) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []
    real = app_metrics.count_lookup

    def record(endpoint: str, status: str) -> None:
        real(endpoint, status)  # still refuses a label outside the lists
        seen.append((endpoint, status))

    monkeypatch.setattr(app_metrics, "count_lookup", record)
    return seen


async def test_each_lookup_counts_once_with_closed_labels(
    client: AsyncClient, lookup_labels: list[tuple[str, str]]
):
    await client.get("/dso/resolve", params={"postcode": "0000"})
    await client.get("/dso/resolve-address", params={"nis_code": "99999"})
    await client.get("/dso/ean/123/check")
    await client.get("/dso/operators")
    await client.get("/dso/meta")
    assert lookup_labels == [
        ("resolve", "not_found"),
        ("resolve_address", "not_found"),
        ("ean_check", "format_invalid"),
    ]
