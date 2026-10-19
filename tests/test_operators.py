"""GET /dso/operators: the catalogue in the app's order.

operators.csv is in display order: the DSOs (`grd`) first, in the order of
crm-frontend's GRD_OPTIONS (ORES first), then the foreign gas operator
(Enexis), then Elia (`grtl`, local transmission, `lookup:false`) last. The
service keeps the file order; the meter form appends Elia as a separate option
and never prefills it.
"""

from httpx import AsyncClient

from api.dso import contract
from tests.conftest import DATA_DIR

FIELDS = {
    "id",
    "kind",
    "name",
    "legal_name",
    "region",
    "elec",
    "gas",
    "website",
    "regulator",
    "tariffs_url",
    "lookup",
    "replaced_by",
}


async def _operators(client: AsyncClient) -> list[dict]:
    response = await client.get("/dso/operators")
    assert response.status_code == 200
    body = response.json()
    assert body["error_code"] == 0
    data: list[dict] = body["data"]
    return data


async def test_the_order_is_the_file_order(client: AsyncClient):
    rows = contract.parse_csv(
        (DATA_DIR / "operators.csv").read_bytes(),
        name="operators.csv",
        header=contract.DIST_OPERATORS_HEADER,
    )
    assert [o["id"] for o in await _operators(client)] == [row["id"] for row in rows]


async def test_dsos_first_then_foreign_gas_then_elia_last(client: AsyncClient):
    operators = await _operators(client)
    kinds = [o["kind"] for o in operators]
    assert kinds == sorted(kinds, key=contract.KIND_RANK.__getitem__)
    assert operators[0]["kind"] == "grd"
    assert operators[0]["id"] == "ores", "GRD_OPTIONS starts with ORES"
    assert operators[-1]["id"] == "elia"


async def test_elia_is_local_transmission_and_never_looked_up(client: AsyncClient):
    elia = next(o for o in await _operators(client) if o["id"] == "elia")
    assert elia["kind"] == "grtl"
    assert elia["lookup"] is False
    assert elia["elec"] is True


async def test_the_shape(client: AsyncClient):
    for operator in await _operators(client):
        assert set(operator) == FIELDS
        for flag in ("elec", "gas", "lookup"):
            assert isinstance(operator[flag], bool)
        assert operator["replaced_by"] is None or isinstance(operator["replaced_by"], str)
        assert operator["kind"] in contract.KIND_RANK


async def test_every_lookup_answer_names_a_lookup_operator(client: AsyncClient, store):
    """No answer ever carries an operator the catalogue marks lookup:false."""
    lookup_ids = {o["id"] for o in await _operators(client) if o["lookup"]}
    for postcode in sorted(store.index.by_postcode):
        data = (await client.get("/dso/resolve", params={"postcode": postcode})).json()["data"]
        for section in data.get("sections") or []:
            assert section["elec_operator"] in lookup_ids
        if data["status"] == "ok":
            result = data["result"]
            assert result["elec"]["operator"] in lookup_ids
            assert result["elec"]["designated"] in lookup_ids
            assert result["gas"]["operator"] in (lookup_ids | {None})
