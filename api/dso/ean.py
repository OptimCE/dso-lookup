"""The HTTP side of the EAN check.

The check itself is the contract's, ported in `api.dso.lookup.check_ean`
(TRIM, `^[0-9]{18}$`, prefix `54`, GS1 weights 3,1,3… from the left, Sibelga
prefix `5414489`). It is INFORMATIVE: crm-backend turns a Sibelga prefix
outside Brussels into a warning, and nothing anywhere refuses an EAN on these
grounds — the Liège demo EANs (RESA) carry the same prefix (Q7).

What this module adds: the answer never echoes the EAN (the reference result
carries it for its CLI). Any length is checked exactly as the reference checks
it, TRIM first: a cap on the raw input refused a padded EAN the reference
accepts, and a long non-EAN costs one strip and one failed fullmatch (the
request line itself is bounded by uvicorn's parser, about 64 KB).
"""

from __future__ import annotations

from api.dso.lookup import check_ean
from api.dso.schemas import EanCheckOut


def ean_check_out(raw: str) -> EanCheckOut:
    check = check_ean(raw)
    return EanCheckOut(
        format=check["format"],
        belgian_prefix=check["belgian_prefix"],
        gs1_check=check["gs1_check"],
        known_operator=check["known_operator"],
    )


def ean_status(check: EanCheckOut) -> str:
    """The vectors' status vocabulary, also the `dso.lookups.total` label."""
    return "format_ok" if check.format else "format_invalid"
