"""Application metrics for the dso-lookup service.

All instruments are created at module import. The OTel API ships a
``_ProxyMeterProvider`` until ``metrics.set_meter_provider(...)`` runs
(inside ``core.tracing.setup_tracer_provider``), and instruments created
against the proxy rebind to the real provider when it is installed —
so import order between this module and tracing setup does not matter.

In LOCAL ``setup_tracer_provider`` returns early and the proxy stays a
no-op, meaning every ``.add(...)`` call below becomes a cheap function
dispatch with no side effects.

Naming follows OTel semantic conventions: dotted lowercase, ``.total``
suffix on monotonically increasing counters.

PRIVACY. These counters are the ONLY trace a lookup leaves. Their labels are
closed vocabularies (an endpoint name, a status), never a postcode, a name, a
NIS code or an EAN: a label value is exported verbatim and kept for the
retention of the metrics backend.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("dso-lookup")

health_checks = _meter.create_counter(
    name="health.checks.total",
    description="Readiness probe component outcomes",
    unit="1",
)

lookups = _meter.create_counter(
    name="dso.lookups.total",
    description=(
        "Lookups answered, labelled by endpoint (resolve, resolve_address, ean_check) "
        "and status (ok, choose_commune, choose_section, not_found, format_ok, "
        "format_invalid)"
    ),
    unit="1",
)

# The label values `lookups` may carry. Anything else is a bug, and the tests
# pin both lists so a free-form value (a postcode...) can never become a label.
LOOKUP_ENDPOINTS = ("resolve", "resolve_address", "ean_check")
LOOKUP_STATUSES = (
    "ok",
    "choose_commune",
    "choose_section",
    "not_found",
    "format_ok",
    "format_invalid",
)


def count_lookup(endpoint: str, status: str) -> None:
    """Add one to ``dso.lookups.total``; refuses a label outside the closed lists."""
    if endpoint not in LOOKUP_ENDPOINTS or status not in LOOKUP_STATUSES:
        raise ValueError(f"not a lookup label: endpoint={endpoint!r} status={status!r}")
    lookups.add(1, {"endpoint": endpoint, "status": status})
