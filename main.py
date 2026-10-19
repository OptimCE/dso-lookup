import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from api.dso.routes import dso_routes
from api.dso.store import load_store
from api.health.routes import health_router, healthz_router
from core.config import Environment, settings
from core.errors.errors import ErrorException
from core.errors.handlers import error_exception_handler, unhandled_exception_handler
from core.logging import configure_logging
from core.middleware.correlation_id import CorrelationIdMiddleware
from core.middleware.locale_middleware import LocaleMiddleware
from core.middleware.request_limits import RequestLimitsMiddleware
from core.tracing import enrich_span, setup_tracer_provider

# dso-lookup is an INTERNAL service, not an annex: crm-backend is its only caller,
# over the Docker network. No CORS, no gateway scope, no database, no NATS — and
# never an entry in ANNEX_CATALOG_* (crm-backend exits at boot on an unknown name).

configure_logging()
logger = logging.getLogger(__name__)

# --- The EAN never reaches a log ---------------------------------------------------------
# billing's shared error paths log `request.url.path`: request_limits on a 413 and a
# 504, the handlers on a domain or an unhandled error. The EAN route carries the EAN
# IN its path. Those files stay identical to billing, so their records are rewritten
# here, by a filter on their own loggers: a logger-level filter runs before any
# handler (stdout in staging, OTLP in production) sees the record.
REDACTED_LOGGERS = (
    "core.middleware.request_limits",
    "core.errors.handlers",
    "core.errors.with_default_error",
)
_EAN_PREFIX = "/dso/ean/"


def redact_path(value: object) -> object:
    """A path under /dso/ean/ becomes its route template; anything else is kept."""
    if isinstance(value, str) and value.startswith(_EAN_PREFIX):
        return "/dso/ean/{ean}/check" if value.endswith("/check") else "/dso/ean/{ean}"
    return value


class EanPathRedaction(logging.Filter):
    """Rewrites `extra={"path": …}` and the %-arguments of a record. Never drops one."""

    def filter(self, record: logging.LogRecord) -> bool:
        if "path" in record.__dict__:  # an `extra=` field
            record.__dict__["path"] = redact_path(record.__dict__["path"])
        if isinstance(record.args, tuple):
            record.args = tuple(redact_path(arg) for arg in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {key: redact_path(arg) for key, arg in record.args.items()}
        return True


for _name in REDACTED_LOGGERS:
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, EanPathRedaction) for f in _logger.filters):
        _logger.addFilter(EanPathRedaction())


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracer_provider()
    # The pinned data release, checked strictly. A DataError here is NOT caught:
    # the process must fail to start rather than answer from a broken directory.
    store = load_store(settings.data_dir)
    app.state.store = store
    logger.info(
        "dso data %s loaded: %s communes, %s rows",
        store.release.version,
        store.counts["communes"],
        store.counts["rows"],
    )
    yield
    app.state.store = None


protected_deps = [Depends(enrich_span)]
app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if settings.ENV == Environment.LOCAL else None,
    redoc_url="/redoc" if settings.ENV == Environment.LOCAL else None,
    openapi_url="/openapi.json" if settings.ENV == Environment.LOCAL else None,
)

# --- Middleware (executed bottom to top — last registered = outermost = executes first) ---
app.add_middleware(LocaleMiddleware)
app.add_middleware(RequestLimitsMiddleware)
app.add_middleware(CorrelationIdMiddleware)

# --- Exception handlers ---
# Starlette's stub types the handler signature as (Request, Exception); FastAPI lets
# you narrow to a specific exception subclass at runtime, which the stub doesn't model.
app.add_exception_handler(ErrorException, error_exception_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_exception_handler)

# --- Routers ---
app.include_router(dso_routes, tags=["DSO"], dependencies=protected_deps)
app.include_router(health_router, prefix="/health", tags=["Health"])
app.include_router(healthz_router, tags=["Health"])

# HTTP server METRICS only. Verified against opentelemetry-instrumentation-asgi
# 0.60b1: the duration histogram's `http.target` is the route's `path_format`
# (`/dso/ean/{ean}/check`), never the path or the query string. Spans would carry
# the full URL, query string included - they are non-recording because no
# TracerProvider is installed (none is, fleet-wide). Whoever installs one must
# exclude /dso/ from them first.
FastAPIInstrumentor.instrument_app(app)

if __name__ == "__main__":
    import uvicorn

    # container-internal API, reached by crm-backend over the Docker network only
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)  # noqa: S104
