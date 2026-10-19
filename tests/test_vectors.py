"""Every row of data/vectors.csv, THROUGH HTTP, with the contract's semantics.

The vectors ship with the data release (belgian-dso-map `tests/vectors.csv`, or
the mini fixture's own): the same rows run against the reference `lookup.py`,
the website's JS port and this service. Here each row becomes the request
crm-backend would make:

- `postcode` -> GET /dso/resolve?postcode=&ins=&section=
- `address`  -> GET /dso/resolve-address?nis_code=&postcode=&post_name=
- `ean`      -> GET /dso/ean/{ean}/check

and the JSON answer is read back into the vectors' terms (status, key, elec,
gas_status, gas, flags) and compared by `lookup.check_observed`: an empty cell
must be empty, `*` is not asserted, key and flags are sets.
"""

from typing import Any
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from api.dso import contract, lookup
from api.dso.store import DsoStore
from tests.conftest import DATA_DIR

_VECTORS = contract.parse_csv(
    (DATA_DIR / "vectors.csv").read_bytes(),
    name="vectors.csv",
    header=contract.VECTORS_HEADER,
    raw_columns=contract.VECTOR_INPUT_COLUMNS,
)


async def _ask(client: AsyncClient, vector: dict[str, str]) -> dict[str, Any]:
    if vector["kind"] == "postcode":
        response = await client.get(
            "/dso/resolve",
            params={
                "postcode": vector["postcode"],
                "ins": vector["ins"],
                "section": vector["section_key"],
            },
        )
    elif vector["kind"] == "address":
        response = await client.get(
            "/dso/resolve-address",
            params={
                "nis_code": vector["ins"],
                "postcode": vector["postcode"],
                "post_name": vector["post_name"],
            },
        )
    else:
        response = await client.get(f"/dso/ean/{quote(vector['ean'], safe='')}/check")
    assert response.status_code == 200, (vector["name"], response.status_code, response.text)
    body = response.json()
    assert body["error_code"] == 0
    data: dict[str, Any] = body["data"]
    return data


def observe_http(kind: str, data: dict[str, Any]) -> dict[str, str]:
    """An HTTP answer in the terms of the vectors' expect_* columns
    (the HTTP twin of the reference `lookup.observe`)."""
    if kind == "ean":
        flags = [
            flag
            for flag, on in (
                ("belgian_prefix", data["belgian_prefix"]),
                ("gs1_ok", data["gs1_check"] is True),
            )
            if on
        ]
        return {
            "status": "format_ok" if data["format"] else "format_invalid",
            "key": "",
            "elec": data["known_operator"] or "",
            "gas_status": "",
            "gas": "",
            "flags": contract.join_list(flags),
        }
    observed = {"status": data["status"], "key": "", "elec": "", "gas_status": "", "gas": ""}
    observed["flags"] = ""
    if data["status"] == "ok":
        result = data["result"]
        section = result["section"]
        observed.update(
            key=result["ins"] + (f"/{section['section_key']}" if section else ""),
            elec=result["elec"]["operator"],
            gas_status=result["gas"]["status"],
            gas=result["gas"]["operator"] or "",
            flags=contract.join_list(result["flags"]),
        )
    elif data["status"] == "choose_commune":
        observed["key"] = contract.join_list([c["ins"] for c in data["communes"]])
    elif data["status"] == "choose_section":
        observed["key"] = contract.join_list([s["section_key"] for s in data["sections"]])
    return observed


def test_the_release_has_vectors_of_every_kind():
    kinds = {vector["kind"] for vector in _VECTORS}
    assert kinds == set(contract.VECTOR_KINDS), kinds
    statuses = {vector["expect_status"] for vector in _VECTORS if vector["kind"] != "ean"}
    assert statuses == set(contract.LOOKUP_STATUSES), statuses


@pytest.mark.parametrize("vector", _VECTORS, ids=[vector["name"] for vector in _VECTORS])
async def test_vector_through_http(client: AsyncClient, vector: dict[str, str]):
    data = await _ask(client, vector)
    problems = lookup.check_observed(vector, observe_http(vector["kind"], data))
    assert not problems, problems


def test_every_vector_in_process(store: DsoStore, vectors: list[dict[str, str]]):
    """The port itself, without HTTP: the reference `run_vectors`."""
    assert lookup.run_vectors(store.index, vectors) == []


@pytest.mark.parametrize("vector", _VECTORS, ids=[vector["name"] for vector in _VECTORS])
async def test_http_and_port_agree_beyond_the_asserted_columns(
    client: AsyncClient, store: DsoStore, vector: dict[str, str]
):
    """Even where a vector says `*`, HTTP answers what the port answers."""
    data = await _ask(client, vector)
    assert observe_http(vector["kind"], data) == lookup.observe(store.index, vector)
