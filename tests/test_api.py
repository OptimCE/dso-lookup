"""The HTTP shapes crm-backend reads (src/modules/dso/domain/dso.types.ts).

The answers are found in the shipped data rather than hard-coded, so these tests
hold on the mini fixture and on the national release alike.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from api.dso.lookup import REGION_REGULATOR
from api.dso.store import DsoStore

RESOLVE_KEYS = {"status", "communes", "sections", "result", "data_version"}
RESULT_KEYS = {
    "ins",
    "commune",
    "section",
    "region",
    "regulator",
    "elec",
    "gas",
    "flags",
    "note",
    "source_ids",
    "valid_from",
    "data_version",
}
COMMUNE_KEYS = {"ins", "names", "region", "province"}
SECTION_KEYS = {"section_key", "names", "postcodes", "elec_operator"}
NAMES_KEYS = {"fr", "nl", "de"}


async def _resolve(client: AsyncClient, **params: str) -> dict[str, Any]:
    response = await client.get("/dso/resolve", params=params)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"data", "error_code"}
    assert body["error_code"] == 0
    data: dict[str, Any] = body["data"]
    assert set(data) == RESOLVE_KEYS
    return data


def _postcode_with(store: DsoStore, predicate) -> str:
    for postcode, rows in sorted(store.index.by_postcode.items()):
        if predicate(rows):
            return postcode
    raise AssertionError("no such postcode in the shipped data")


async def test_choose_commune(client: AsyncClient, store: DsoStore):
    postcode = _postcode_with(store, lambda rows: len({r["ins"] for r in rows}) > 1)
    data = await _resolve(client, postcode=postcode)
    assert data["status"] == "choose_commune"
    assert data["sections"] is None and data["result"] is None
    inses = [c["ins"] for c in data["communes"]]
    assert inses == sorted(inses)
    for commune in data["communes"]:
        assert set(commune) == COMMUNE_KEYS
        assert set(commune["names"]) == NAMES_KEYS
        assert (commune["province"] == "") == (commune["region"] == "BRU")


async def test_choose_section_names_its_commune_and_every_section(
    client: AsyncClient, store: DsoStore
):
    postcode = _postcode_with(
        store, lambda rows: len({r["ins"] for r in rows}) == 1 and len(rows) > 1
    )
    data = await _resolve(client, postcode=postcode)
    assert data["status"] == "choose_section"
    assert data["result"] is None
    # crm-backend reads communes[0] to retry through the address route.
    assert len(data["communes"]) == 1
    ins = data["communes"][0]["ins"]
    keys = [s["section_key"] for s in data["sections"]]
    assert keys == sorted(keys)
    for section in data["sections"]:
        assert set(section) == SECTION_KEYS
        expected = store.sections[(ins, section["section_key"])]
        assert section["postcodes"] == list(expected.postcodes)
        assert postcode in section["postcodes"]
        assert section["elec_operator"] == expected.elec_operator


async def test_ok(client: AsyncClient, store: DsoStore):
    for postcode in sorted(store.index.by_postcode):
        data = await _resolve(client, postcode=postcode)
        if data["status"] != "ok":
            continue
        assert data["communes"] is None and data["sections"] is None
        result = data["result"]
        assert set(result) == RESULT_KEYS
        row = store.index.by_postcode[postcode][0]
        assert result["regulator"] == REGION_REGULATOR[result["region"]]
        assert result["elec"] == {
            "operator": row["elec_operator"],
            "designated": row["elec_designated"],
        }
        assert result["gas"]["status"] == row["gas_status"]
        assert result["gas"]["operator"] == (row["gas_operator"] or None)
        assert result["valid_from"] == (row["valid_from"] or None)
        assert result["data_version"] == data["data_version"] == store.release.version
        if row["note_key"]:
            note = store.notes[row["note_key"]]
            assert result["note"] == {
                "key": note.key,
                "fr": note.fr,
                "nl": note.nl,
                "de": note.de,
                "en": note.en,
            }
        else:
            assert result["note"] is None
        if row["section_key"]:
            assert set(result["section"]) == SECTION_KEYS
        else:
            assert result["section"] is None


async def test_a_section_result_with_its_note(client: AsyncClient, store: DsoStore):
    """Picking a section with a note: the note's text travels with the result."""
    rows = [r for rs in store.index.by_postcode.values() for r in rs]
    row = next((r for r in rows if r["section_key"] and r["note_key"]), None)
    if row is None:
        pytest.skip("no section carries a note in this release")
    data = await _resolve(
        client, postcode=row["postcode"], ins=row["ins"], section=row["section_key"]
    )
    assert data["status"] == "ok"
    assert data["result"]["section"]["section_key"] == row["section_key"]
    assert data["result"]["note"]["key"] == row["note_key"]
    assert data["result"]["note"]["fr"]


async def test_empty_names_fall_back_to_french(client: AsyncClient, store: DsoStore):
    rows = [r for rs in store.index.by_postcode.values() for r in rs]
    row = next(
        (r for r in rows if r["section_key"] and not (r["section_nl"] and r["section_de"])), None
    )
    if row is None:
        pytest.skip("every section name is filled in this release")
    data = await _resolve(
        client, postcode=row["postcode"], ins=row["ins"], section=row["section_key"]
    )
    names = data["result"]["section"]["names"]
    assert names["fr"] == row["section_fr"]
    for language in ("nl", "de"):
        assert names[language] == (row[f"section_{language}"] or row["section_fr"]), language


async def test_not_found(client: AsyncClient, store: DsoStore):
    data = await _resolve(client, postcode="0000")
    assert data == {
        "status": "not_found",
        "communes": None,
        "sections": None,
        "result": None,
        "data_version": store.release.version,
    }


async def test_meta(client: AsyncClient, store: DsoStore):
    response = await client.get("/dso/meta")
    assert response.status_code == 200
    assert response.json() == {
        "data": {
            "data_version": store.release.version,
            "version_date": store.release.date,
            "communes": store.release.counts["communes"],
            "communes_by_region": store.release.counts["communes_by_region"],
            "rows": store.release.counts["rows"],
            "operators": store.release.counts["operators"],
        },
        "error_code": 0,
    }


async def test_no_docs_outside_local(client: AsyncClient):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


async def test_get_only(client: AsyncClient):
    response = await client.post("/dso/resolve", json={"postcode": "1000"})
    assert response.status_code == 405


async def test_a_request_id_is_returned(client: AsyncClient):
    response = await client.get("/dso/meta", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"
