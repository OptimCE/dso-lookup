"""The EAN check: informative, never blocking, and never echoing the EAN."""

from urllib.parse import quote

import pytest
from httpx import AsyncClient

from api.dso.ean import ean_check_out
from api.dso.lookup import check_ean

FULLWIDTH_EAN = "".join(chr(ord(digit) + 0xFEE0) for digit in "541448200000000008")


@pytest.mark.parametrize(
    ("raw", "fmt", "prefix", "gs1", "operator"),
    [
        ("541448200000000001", True, True, False, None),  # the dev EAN: wrong check digit
        ("541448200000000008", True, True, True, None),
        ("541448900000000007", True, True, True, "sibelga"),
        ("541448920000100002", True, True, True, "sibelga"),  # Liège demo (RESA), Q7
        ("871685900000000004", True, False, True, None),  # not Belgian
        ("  541448200000000008 ", True, True, True, None),  # TRIMmed
        ("\u00a0541448200000000008\ufeff", True, True, True, None),
        ("54144820000000000", False, False, None, None),  # 17 digits
        ("5414482000000", False, False, None, None),  # 13 digits
        ("54144820000000000A", False, False, None, None),
        ("5414 4820 0000 0000 08", False, False, None, None),  # inner spaces kept
        (FULLWIDTH_EAN, False, False, None, None),  # full-width digits
        ("", False, False, None, None),
    ],
)
def test_check_ean(raw, fmt, prefix, gs1, operator):
    check = check_ean(raw)
    assert (check["format"], check["belgian_prefix"], check["gs1_check"]) == (fmt, prefix, gs1)
    assert check["known_operator"] == operator
    out = ean_check_out(raw)
    assert out.model_dump() == {
        "format": fmt,
        "belgian_prefix": prefix,
        "gs1_check": gs1,
        "known_operator": operator,
    }


def test_any_length_is_checked_as_the_reference_checks_it():
    """TRIM first, whatever the length: 47 spaces + an EAN (65 characters) is a valid
    EAN for the reference. A cap of 64 characters on the RAW input answered
    `format:false` to it. A long non-EAN costs a strip and one failed fullmatch."""
    padded = " " * 47 + "541448200000000008"
    assert len(padded) == 65
    out = ean_check_out(padded)
    reference = check_ean(padded)
    assert (out.format, out.gs1_check) == (True, True)
    assert out.model_dump() == {k: v for k, v in reference.items() if k != "ean"}
    assert ean_check_out("5" * 60_000).format is False


async def test_a_long_padded_ean_through_http(client: AsyncClient):
    response = await client.get("/dso/ean/" + "%20" * 47 + "541448200000000008/check")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "format": True,
        "belgian_prefix": True,
        "gs1_check": True,
        "known_operator": None,
    }


async def test_the_answer_never_echoes_the_ean(client: AsyncClient):
    ean = "541448200000000008"
    response = await client.get(f"/dso/ean/{ean}/check")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "data": {
            "format": True,
            "belgian_prefix": True,
            "gs1_check": True,
            "known_operator": None,
        },
        "error_code": 0,
    }
    assert ean not in response.text


async def test_an_empty_or_odd_segment_is_an_answer(client: AsyncClient):
    for path in (
        "/dso/ean//check",
        f"/dso/ean/{quote('a/b', safe='')}/check",
        "/dso/ean/a/b/check",
    ):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.json()["data"]["format"] is False


async def test_the_ean_check_needs_no_data(client: AsyncClient):
    """It is pure: it answers even if the store were missing."""
    from main import app

    store = app.state.store
    app.state.store = None
    try:
        response = await client.get("/dso/ean/541448200000000008/check")
        assert response.status_code == 200
    finally:
        app.state.store = store
