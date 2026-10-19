"""Normalisation and the lookup rules, as the contract defines them.

TRIM removes exactly [ \\t\\r\\n\\u00a0\\ufeff] at both ends; a postcode is
`^[0-9]{4}$` after TRIM (never `\\d`, which takes full-width digits in Python);
a name is NFKD, without combining marks, lower(), then only [a-z0-9].
"""

import pytest
from httpx import AsyncClient

from api.dso import lookup
from api.dso.store import DsoStore


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5020", "5020"),
        (" 5020 ", "5020"),
        ("\t5020\r\n", "5020"),
        ("\u00a05020", "5020"),  # no-break space: trimmed
        ("\ufeff5020", "5020"),  # BOM: trimmed (Python's strip() keeps it)
        ("\u20035020", None),  # em space: NOT trimmed (JavaScript's trim() removes it)
        ("\uff15\uff10\uff12\uff10", None),  # full-width digits: not ASCII
        ("502", None),
        ("50200", None),
        ("5O20", None),
        ("50 20", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_postcode(raw, expected):
    assert lookup.normalize_postcode(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Marche lès Dames", "marchelesdames"),
        ("MARCHE-LES-DAMES", "marchelesdames"),
        ("Sint-Jans-Molenbeek", "sintjansmolenbeek"),
        ("sint jans molenbeek", "sintjansmolenbeek"),
        ("Liège", "liege"),
        ("Écaussinnes-d'Enghien", "ecaussinnesdenghien"),
        ("Écaussinnes-d\u2019Enghien", "ecaussinnesdenghien"),
        ("Namür", "namur"),
        ("Gelbressée", "gelbressee"),
        ("ﬂawinne", "flawinne"),  # NFKD splits the ligature
        ("  ", ""),
        (None, ""),
    ],
)
def test_normalize_name(raw, expected):
    assert lookup.normalize_name(raw) == expected


def test_trim_is_exactly_the_contract_set():
    assert lookup.TRIM_CHARS == " \t\r\n\u00a0\ufeff"
    assert lookup.trim("\u2003x\u2003") == "\u2003x\u2003"


def test_the_commune_name_never_picks_a_section(store: DsoStore):
    """BeST's postal name in Flanders is often the commune's: an address in
    Zondereigen can come back as "Baarle-Hertog". A post_name equal to the
    commune's FR, NL or DE name therefore answers choose_section."""
    for ins, rows in store.index.by_ins.items():
        by_postcode: dict[str, list] = {}
        for row in rows:
            by_postcode.setdefault(row["postcode"], []).append(row)
        for postcode, candidates in by_postcode.items():
            if len(candidates) < 2:
                continue
            commune = store.communes[ins]
            for name in (commune.names.fr, commune.names.nl, commune.names.de):
                found = lookup.resolve_address(store.index, ins, postcode, name)
                assert found["status"] == "choose_section", (ins, postcode, name)


def test_a_single_candidate_wins_whatever_the_post_name(store: DsoStore):
    for ins, rows in store.index.by_ins.items():
        postcodes = [row["postcode"] for row in rows]
        for postcode in {p for p in postcodes if postcodes.count(p) == 1}:
            found = lookup.resolve_address(store.index, ins, postcode, "Nowhere At All")
            assert found["status"] == "ok", (ins, postcode)


def test_gs1_check_digit():
    assert lookup.gs1_check_digit("54144820000000000") == 8
    assert lookup.check_ean("541448200000000001")["gs1_check"] is False
    assert lookup.check_ean("541448200000000008")["gs1_check"] is True


# --- Through HTTP ------------------------------------------------------------------------------


async def test_a_malformed_postcode_is_an_answer_not_a_422(client: AsyncClient):
    for postcode in ("", "abc", "5O20", "50200", "x" * 500, "\uff11\uff10\uff10\uff10"):
        response = await client.get("/dso/resolve", params={"postcode": postcode})
        assert response.status_code == 200, postcode
        assert response.json()["data"]["status"] == "not_found"


async def test_absent_parameters_are_not_found(client: AsyncClient):
    for path in ("/dso/resolve", "/dso/resolve-address"):
        response = await client.get(path)
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "not_found"


async def test_the_nis_code_is_compared_as_sent(client: AsyncClient, store: DsoStore):
    """TRIM, then an exact comparison: no padding. Every INS is `[1-9][0-9]{4}`, so
    an int form is the same 5-digit string, and a short or zero-padded value is a
    `not_found` answer (never a 422) whether it is padded or not."""
    ins = next(iter(store.communes))
    padded = await client.get("/dso/resolve-address", params={"nis_code": ins})
    assert padded.json()["data"]["status"] != "not_found"
    for raw in ("1", "21", "0", "9999", "09209", ins[1:], "0" + ins):
        response = await client.get("/dso/resolve-address", params={"nis_code": raw})
        assert response.status_code == 200
        assert response.json()["data"]["status"] == "not_found"
    assert lookup.trim(" " + ins + " ") == ins
    padded_spaces = await client.get("/dso/resolve-address", params={"nis_code": f" {ins} "})
    assert padded_spaces.json()["data"]["status"] == padded.json()["data"]["status"]
