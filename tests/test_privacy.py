"""Nothing a caller sends is written anywhere.

A lookup carries a postcode, a NIS code, a postal name and an EAN — a home, in
other words. The service keeps none of it:

- no route logs a parameter (checked here with every logger at DEBUG);
- uvicorn's access log, which would print `GET /dso/resolve?postcode=…` and, in
  production, export it over OTLP, is off: `--no-access-log` in BOTH Dockerfiles;
- the only trace is the `dso.lookups.total{endpoint,status}` counter, whose labels
  are closed vocabularies.
"""

import logging
import re
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from api.dso import routes
from core import metrics as app_metrics
from core.middleware import request_limits
from main import REDACTED_LOGGERS, EanPathRedaction, app, redact_path
from tests.conftest import REPO_ROOT

POSTCODE = "5020"
NIS = "92094"
POST_NAME = "Flawinne-Zzyzx-Privacy"
EAN = "541448200000000008"
# Four ASCII digits, so the lookup searches for it, and in no release ever: the
# contract's postcodes start with 1-9 (8791, used here before, is Beveren-Leie).
UNKNOWN_POSTCODE = "0791"
_STANDARD = set(vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None))) | {
    "message",
    "asctime",
}


async def test_no_parameter_reaches_the_logs(client: AsyncClient, caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG)
    await client.get("/dso/resolve", params={"postcode": POSTCODE, "ins": NIS, "section": "zzz"})
    await client.get("/dso/resolve", params={"postcode": UNKNOWN_POSTCODE})
    await client.get(
        "/dso/resolve-address",
        params={"nis_code": NIS, "postcode": POSTCODE, "post_name": POST_NAME},
    )
    await client.get(f"/dso/ean/{EAN}/check")

    # The error path logs (core/errors/handlers.py): it must log the path only.
    # Also the positive control - it proves server-side records ARE captured here.
    store = app.state.store
    app.state.store = None
    try:
        refused = await client.get(
            "/dso/resolve-address",
            params={"nis_code": NIS, "postcode": POSTCODE, "post_name": POST_NAME},
        )
    finally:
        app.state.store = store
    assert refused.status_code == 503

    records = _server_records(caplog)
    assert any("2600" in r.getMessage() for r in records), "no server-side record captured"
    text = _logged_text(records)
    for value in (POSTCODE, UNKNOWN_POSTCODE, POST_NAME, EAN, "Zzyzx"):
        assert value not in text, f"{value!r} reached a log record"


async def test_the_ean_never_reaches_a_log_on_the_error_paths(
    client: AsyncClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """billing's shared error paths log `request.url.path`, and the EAN route carries
    the EAN IN its path: a 413 (a body on a GET), a 504 (a stalled handler) and a
    domain error all logged it. main.py rewrites it to the route template."""
    caplog.set_level(logging.DEBUG)
    path = f"/dso/ean/{EAN}/check"

    too_large = await client.request(
        "GET", path, content=b"x" * (request_limits.MAX_BODY_BYTES + 1)
    )
    assert too_large.status_code == 413

    async def stalled(awaitable, **_):  # what wait_for does when the handler hangs
        awaitable.close()
        raise TimeoutError

    with monkeypatch.context() as patch:
        patch.setattr(request_limits, "asyncio", SimpleNamespace(wait_for=stalled))
        timed_out = await client.get(path)
    assert timed_out.status_code == 504

    def broken(raw: str):
        raise RuntimeError("simulated failure of the check")

    with monkeypatch.context() as patch:
        patch.setattr(routes, "ean_check_out", broken)
        failed = await client.get(path)
    assert failed.status_code == 400  # with_default_error promotes it to EAN_CHECK

    text = _logged_text(_server_records(caplog))
    for message in ("body too large", "timed out", "Domain error"):
        assert message in text, f"no {message!r} record: that error path was not exercised"
    assert "/dso/ean/{ean}/check" in text, "the route template is what gets logged"
    assert EAN not in text, "the EAN reached a log record"


@pytest.mark.parametrize(
    ("path", "logged"),
    [
        (f"/dso/ean/{EAN}/check", "/dso/ean/{ean}/check"),
        (f"/dso/ean/{EAN}/", "/dso/ean/{ean}"),
        ("/dso/ean/a/b/check", "/dso/ean/{ean}/check"),
        ("/dso/ean//check", "/dso/ean/{ean}/check"),
        ("/dso/resolve", "/dso/resolve"),
        ("/health/readiness", "/health/readiness"),
    ],
)
def test_redact_path(path: str, logged: str):
    assert redact_path(path) == logged


def test_the_redaction_sits_on_every_logger_that_logs_a_path():
    """A logger-level filter runs before any handler: stdout in staging, OTLP in
    production. Every module of core/ that logs `request.url.path` is covered."""
    logging_a_path = sorted(
        "core." + ".".join(path.relative_to(REPO_ROOT / "core").with_suffix("").parts)
        for path in (REPO_ROOT / "core").rglob("*.py")
        if "url.path" in path.read_text(encoding="utf-8")
    )
    assert logging_a_path, "the scan found nothing: it is broken"
    assert set(logging_a_path) <= set(REDACTED_LOGGERS)
    for name in REDACTED_LOGGERS:
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, EanPathRedaction) for f in filters) == 1, name

    ean_path = f"/dso/ean/{EAN}/check"
    record = logging.LogRecord("x", logging.WARNING, "x", 0, "on %s", (ean_path,), None)
    record.path = ean_path
    assert EanPathRedaction().filter(record)
    assert EAN not in record.getMessage()
    assert EAN not in record.path


def _server_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """The test's own HTTP client (httpx) logs every URL it sends: that is the
    caller's side, not this service, which has no HTTP client at all."""
    return [r for r in caplog.records if not r.name.startswith(("httpx", "httpcore"))]


def _logged_text(records: list[logging.LogRecord]) -> str:
    """The message, its arguments, any `extra=` field and a traceback - not the
    record's timestamps and thread ids, which may contain "5020" by chance."""
    return "\n".join(
        f"{record.getMessage()} {record.exc_text or ''} "
        f"{ {k: v for k, v in record.__dict__.items() if k not in _STANDARD} }"
        for record in records
    )


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "Dockerfile.production"])
def test_both_images_run_uvicorn_without_an_access_log(dockerfile: str):
    text = (REPO_ROOT / dockerfile).read_text(encoding="utf-8")
    cmd = [line for line in text.splitlines() if line.startswith("CMD")]
    assert len(cmd) == 1, f"{dockerfile} must have exactly one CMD"
    assert "uvicorn" in cmd[0]
    assert '"--no-access-log"' in cmd[0], f"{dockerfile} would log every query string"


def test_the_logging_config_also_silences_the_access_logger():
    from core.logging import configure_logging

    configure_logging()
    assert logging.getLogger("uvicorn.access").getEffectiveLevel() >= logging.WARNING


def test_lookup_labels_are_closed_vocabularies():
    with pytest.raises(ValueError, match="not a lookup label"):
        app_metrics.count_lookup("resolve", POSTCODE)
    with pytest.raises(ValueError, match="not a lookup label"):
        app_metrics.count_lookup(POSTCODE, "ok")
    for endpoint in app_metrics.LOOKUP_ENDPOINTS:
        assert re.fullmatch(r"[a-z_]+", endpoint)
    for status in app_metrics.LOOKUP_STATUSES:
        assert re.fullmatch(r"[a-z_]+", status)


def test_no_route_module_logs():
    """The DSO modules hold no logger at all: nothing to leak through."""
    for name in ("routes", "lookup", "schemas", "ean", "deps", "store"):
        source = (REPO_ROOT / "api" / "dso" / f"{name}.py").read_text(encoding="utf-8")
        assert "getLogger" not in source, name
        assert "print(" not in source, name
