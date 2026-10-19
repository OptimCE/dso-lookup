from core.errors.errors import Error


# ---------------------------------------------------------------------------
# DSO lookup (2600+). Every key here must have a matching entry in every locale
# file — tests/test_locales.py enforces it.
#
# crm-backend treats ANY non-200 from this service as "unavailable" and leaves
# the GRD to the user, so these codes are for logs and for a human reading a
# response, never for a caller to branch on.
# ---------------------------------------------------------------------------
class _DsoErrors:
    # The store is not loaded (raised with HTTP 503). In a running container this
    # cannot happen — a data directory that fails the checks fails the boot — but
    # the dependency still refuses rather than answering from nothing.
    DATA_UNAVAILABLE = Error(code=2600, key="ERRORS.DSO.DATA_UNAVAILABLE")

    # Defaults for `with_default_error`: an unexpected failure inside a route.
    RESOLVE = Error(code=2601, key="ERRORS.DSO.RESOLVE")
    RESOLVE_ADDRESS = Error(code=2602, key="ERRORS.DSO.RESOLVE_ADDRESS")
    OPERATORS = Error(code=2603, key="ERRORS.DSO.OPERATORS")
    EAN_CHECK = Error(code=2604, key="ERRORS.DSO.EAN_CHECK")
    META = Error(code=2605, key="ERRORS.DSO.META")


class _Errors:
    dso = _DsoErrors()


errors = _Errors()
