"""The regulator of a result comes from the contract's hard-coded region map.

belgian-dso-map `REGION_REGULATOR` (ported in api/dso/lookup.py) uses the codes
of the monorepo's `reference/regulators.json`, and ignores its `active` flags:
VREG and BRUGEL are `active: false` there (billing's parity gate and
crm-backend's `isActiveRegulator` read that flag), yet a Flemish or Brussels
address still has its regulator.

The registry is vendored as tests/fixtures/regulators.json, so this repository's
own CI (no monorepo checkout) still checks the codes. When the monorepo copy is
present it must equal the vendored one, so the vendored copy cannot drift.
"""

import json
from pathlib import Path

import pytest

from api.dso.lookup import REGION_REGULATOR
from api.dso.store import DsoStore
from tests.conftest import REPO_ROOT

VENDORED = REPO_ROOT / "tests" / "fixtures" / "regulators.json"
MONOREPO_COPY = REPO_ROOT.parent / "reference" / "regulators.json"
REGION_OF = {"Wallonia": "WAL", "Flanders": "VLA", "Brussels": "BRU"}


def _registry(path: Path) -> list[dict]:
    content = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(content, list)
    return content


def test_every_region_maps_to_a_known_regulator_code():
    registry = _registry(VENDORED)
    by_region = {REGION_OF[entry["region"]]: entry["code"] for entry in registry}
    assert by_region == REGION_REGULATOR


def test_the_active_flag_changes_nothing():
    registry = _registry(VENDORED)
    inactive = {entry["code"] for entry in registry if not entry["active"]}
    assert inactive, "the registry has no inactive regulator: re-read this test"
    assert inactive <= set(REGION_REGULATOR.values())


@pytest.mark.skipif(not MONOREPO_COPY.is_file(), reason="not inside the monorepo")
def test_the_vendored_registry_is_the_monorepo_one():
    assert _registry(VENDORED) == _registry(MONOREPO_COPY)


def test_operators_name_known_regulators(store: DsoStore):
    for operator in store.operators:
        assert operator.regulator in REGION_REGULATOR.values(), operator.id


async def test_results_carry_the_regulator_of_their_region(client, store: DsoStore):
    for postcode in sorted(store.index.by_postcode):
        body = (await client.get("/dso/resolve", params={"postcode": postcode})).json()["data"]
        if body["status"] == "ok":
            result = body["result"]
            assert result["regulator"] == REGION_REGULATOR[result["region"]]
