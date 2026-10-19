"""The port answers what the reference answers.

`api/dso/lookup.py` is a line-for-line port of belgian-dso-map's
`scripts/lookup.py`. When that repository is checked out next to the monorepo
(`../../belgian-dso-map` from here, or `DSO_MAP_REPO`), this file imports the
reference and runs both on the release's vectors and on a grid of awkward
inputs, comparing whole results. Skipped elsewhere (this repository's own CI
does not check belgian-dso-map out).
"""

import importlib
import itertools
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from api.dso import lookup
from api.dso.store import DsoStore
from tests.conftest import REPO_ROOT

_DEFAULT_MAP = REPO_ROOT.parent.parent / "belgian-dso-map"
MAP_REPO = Path(os.environ.get("DSO_MAP_REPO", str(_DEFAULT_MAP)))
REFERENCE = MAP_REPO / "scripts" / "lookup.py"

pytestmark = pytest.mark.skipif(
    not REFERENCE.is_file(), reason=f"belgian-dso-map is not checked out at {MAP_REPO}"
)


@pytest.fixture(scope="module")
def reference() -> Iterator[ModuleType]:
    """The reference module, imported from its own scripts/ (it imports csvio, schema)."""
    scripts = str(REFERENCE.parent)
    names = ("lookup", "csvio", "schema")
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    sys.path.insert(0, scripts)
    try:
        module = importlib.import_module("lookup")
        yield module
    finally:
        sys.path.remove(scripts)
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_the_constants_are_the_reference(reference: ModuleType):
    assert lookup.REGION_REGULATOR == reference.REGION_REGULATOR
    assert lookup.TRIM_CHARS == reference.TRIM_CHARS
    assert lookup.SIBELGA_EAN_PREFIX == reference.SIBELGA_EAN_PREFIX
    assert lookup.ANY == reference.ANY


def test_every_vector_observes_the_same(
    reference: ModuleType, store: DsoStore, vectors: list[dict[str, str]]
):
    ref_index = reference.build_index(
        row for rows in store.index.by_postcode.values() for row in rows
    )
    for vector in vectors:
        assert lookup.observe(store.index, vector) == reference.observe(ref_index, vector), vector[
            "name"
        ]


NBSP, BOM, EM_SPACE = chr(0xA0), chr(0xFEFF), chr(0x2003)


def _fullwidth(digits: str) -> str:
    return "".join(chr(ord(digit) + 0xFEE0) for digit in digits)


_POSTCODES = [
    "",
    " 1000 ",
    NBSP + "5020",
    BOM + "1300",
    EM_SPACE + "5020",
    _fullwidth("5020"),
    "502",
    "9999",
]
_NAMES = ["", "Namur", "NAMEN", "Flawinne", "zondereigen", "Baerle-Duc", "Marche lès Dames", "x"]


def test_resolve_and_resolve_address_agree_on_a_grid(reference: ModuleType, store: DsoStore):
    rows = [row for rows in store.index.by_postcode.values() for row in rows]
    ref_index = reference.build_index(rows)
    postcodes = [*sorted({row["postcode"] for row in rows}), *_POSTCODES]
    inses = [*sorted({row["ins"] for row in rows}), "", "99999", " 21004 "]
    sections = [*sorted({row["section_key"] for row in rows}), "zzz"]
    for postcode, ins in itertools.product(postcodes, inses):
        for section in sections:
            assert lookup.resolve(store.index, postcode, ins, section) == reference.resolve(
                ref_index, postcode, ins, section
            ), (postcode, ins, section)
        for name in _NAMES:
            assert lookup.resolve_address(
                store.index, ins, postcode, name
            ) == reference.resolve_address(ref_index, ins, postcode, name), (ins, postcode, name)


def test_check_ean_agrees(reference: ModuleType):
    for raw in (
        "",
        "541448200000000001",
        "541448200000000008",
        "  541448900000000007 ",
        "5414 4820 0000 0000 08",
        "54144820000000000A",
        "871685900000000004",
        _fullwidth("541448200000000008"),
        None,
    ):
        assert lookup.check_ean(raw) == reference.check_ean(raw), raw
