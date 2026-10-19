"""Lookup: postcode or address -> distribution system operators, and the EAN check.

A LINE-FOR-LINE PORT of belgian-dso-map `scripts/lookup.py`, the reference
implementation of the contract (its README, "Lookup algorithm"). The website
(`assets/js/dso-lookup-core.js`) ports the same file, and the release's
`vectors.csv` runs against all three. Change nothing here that the reference
does not change first: a change of the algorithm is a change in three
repositories, made in the same series of pull requests.

What differs from the reference, and why:
- `csvio`/`schema` helpers come from `api.dso.contract`;
- the loader (`load_index`, strict CSV) lives in `api.dso.store`, and the CLI is
  not ported (this service answers over HTTP);
- the results keep the REFERENCE shape. `api.dso.schemas` turns them into the
  HTTP shape crm-backend reads, so this file stays comparable with the
  reference (`tests/test_lookup_reference.py` runs both on the same inputs when
  belgian-dso-map is checked out next to the monorepo).

Privacy: nothing in this module logs. Inputs are a postcode, a NIS code, a
postal name and an EAN.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from api.dso.contract import join_list, split_list

Row = Mapping[str, str]

# Regulator of each region (codes of monorepo reference/regulators.json; `active` is ignored).
REGION_REGULATOR = {"BRU": "BE-BRU-BRUGEL", "VLA": "BE-VLA-VREG", "WAL": "BE-WAL-CWAPE"}

# TRIM removes exactly these characters at both ends. Python's strip() keeps U+FEFF and
# JavaScript's trim() removes U+2003, so neither is used as is.
TRIM_CHARS = " \t\r\n\u00a0\ufeff"

# Informative only (Q7): the Liège demo EANs (RESA) also start with these digits.
SIBELGA_EAN_PREFIX = "5414489"

ANY = "*"  # vectors.csv: "not asserted"

_POSTCODE = re.compile(r"[0-9]{4}")
_EAN = re.compile(r"[0-9]{18}")
_NOT_ALNUM = re.compile(r"[^a-z0-9]")


def trim(raw: str | None) -> str:
    return (raw or "").strip(TRIM_CHARS)


def normalize_postcode(raw: str | None) -> str | None:
    """TRIM, then exactly four ASCII digits; anything else is None."""
    value = trim(raw)
    return value if _POSTCODE.fullmatch(value) else None


def normalize_name(raw: str | None) -> str:
    """NFKD, drop combining marks, lower(), keep [a-z0-9].

    "Marche lès Dames" and "MARCHE-LES-DAMES" both give "marchelesdames".
    """
    decomposed = unicodedata.normalize("NFKD", raw or "")
    unmarked = "".join(ch for ch in decomposed if not unicodedata.category(ch).startswith("M"))
    return _NOT_ALNUM.sub("", unmarked.lower())


@dataclass(frozen=True)
class Index:
    """dso_lookup.csv rows indexed by postcode and by INS (file order kept)."""

    by_postcode: dict[str, list[Row]]
    by_ins: dict[str, list[Row]]
    data_version: str


def build_index(rows: Iterable[Row]) -> Index:
    by_postcode: dict[str, list[Row]] = {}
    by_ins: dict[str, list[Row]] = {}
    data_version = ""
    for row in rows:
        by_postcode.setdefault(row["postcode"], []).append(row)
        by_ins.setdefault(row["ins"], []).append(row)
        data_version = row["data_version"]
    return Index(by_postcode=by_postcode, by_ins=by_ins, data_version=data_version)


# --- Results ------------------------------------------------------------------------------


def _names(row: Row, prefix: str) -> dict[str, str]:
    return {"fr": row[f"{prefix}_fr"], "nl": row[f"{prefix}_nl"], "de": row[f"{prefix}_de"]}


def _normalized_names(row: Row, prefix: str) -> set[str]:
    return {normalize_name(name) for name in _names(row, prefix).values()} - {""}


def _not_found(ix: Index) -> dict[str, Any]:
    return {"status": "not_found", "data_version": ix.data_version}


def _choose_commune(ix: Index, rows: Sequence[Row]) -> dict[str, Any]:
    first_by_ins = {row["ins"]: row for row in reversed(rows)}
    communes = [
        {"ins": ins, "name": _names(first_by_ins[ins], "commune")} for ins in sorted(first_by_ins)
    ]
    return {"status": "choose_commune", "communes": communes, "data_version": ix.data_version}


def _choose_section(ix: Index, rows: Sequence[Row]) -> dict[str, Any]:
    """Sections sorted by key; each carries its electricity operator (the app prefills
    when every candidate agrees, as in Baarle-Hertog)."""
    sections = [
        {
            "section_key": row["section_key"],
            "name": _names(row, "section"),
            "elec_operator": row["elec_operator"],
        }
        for row in sorted(rows, key=lambda row: row["section_key"])
    ]
    return {
        "status": "choose_section",
        "ins": rows[0]["ins"],
        "commune": _names(rows[0], "commune"),
        "sections": sections,
        "data_version": ix.data_version,
    }


def _ok(ix: Index, row: Row) -> dict[str, Any]:
    section = (
        {"section_key": row["section_key"], "name": _names(row, "section")}
        if row["section_key"]
        else None
    )
    result = {
        "postcode": row["postcode"],
        "ins": row["ins"],
        "commune": _names(row, "commune"),
        "section": section,
        "region": row["region"],
        "province": row["province"],
        "regulator": REGION_REGULATOR[row["region"]],
        "elec": {"operator": row["elec_operator"], "designated": row["elec_designated"]},
        "gas": {"status": row["gas_status"], "operator": row["gas_operator"]},
        "flags": split_list(row["flags"]),
        "note_key": row["note_key"],
        "source_ids": split_list(row["source_ids"]),
        "valid_from": row["valid_from"],
        "data_version": row["data_version"],
    }
    return {"status": "ok", "result": result, "data_version": ix.data_version}


# --- Lookups ------------------------------------------------------------------------------


def resolve(
    ix: Index, postcode: str | None, ins: str | None = None, section: str | None = None
) -> dict[str, Any]:
    """Postcode route (website and app).

    1. Invalid postcode, or no row: not_found.
    2. An `ins` or `section` filter that matches nothing is ignored. `section` applies
       only once a single INS remains.
    3. Several INS: choose_commune.
    4. One INS, several sections: choose_section.
    5. Otherwise: ok.
    """
    pc = normalize_postcode(postcode)
    rows = ix.by_postcode.get(pc, []) if pc else []
    if not rows:
        return _not_found(ix)
    wanted_ins = trim(ins)
    if wanted_ins:
        rows = [row for row in rows if row["ins"] == wanted_ins] or rows
    if len({row["ins"] for row in rows}) > 1:
        return _choose_commune(ix, rows)
    wanted_section = trim(section)
    if wanted_section:
        rows = [row for row in rows if row["section_key"] == wanted_section] or rows
    if len(rows) > 1:
        return _choose_section(ix, rows)
    return _ok(ix, rows[0])


def resolve_address(
    ix: Index, nis_code: str | None, postcode: str | None = None, post_name: str | None = None
) -> dict[str, Any]:
    """Address route (app, BeST address: nisCode, postcode, postal name).

    1. Candidates: the rows of the INS with that postcode; if none, every section of
       the INS. An unknown INS: not_found.
    2. One candidate: ok, whatever the postal name says.
    3. Several candidates: an empty postal name, or one equal to the commune's name,
       gives choose_section (BeST's postal name in Flanders is often the commune name).
       A postal name equal to the name of exactly one candidate gives ok; anything
       else gives choose_section.
    """
    rows_of_ins = ix.by_ins.get(trim(nis_code), [])
    if not rows_of_ins:
        return _not_found(ix)
    pc = normalize_postcode(postcode)
    candidates = [row for row in rows_of_ins if row["postcode"] == pc]
    if not candidates:
        candidates = list({row["section_key"]: row for row in reversed(rows_of_ins)}.values())
    if len(candidates) == 1:
        return _ok(ix, candidates[0])
    wanted = normalize_name(post_name)
    if wanted and wanted not in _normalized_names(candidates[0], "commune"):
        matches = [row for row in candidates if wanted in _normalized_names(row, "section")]
        if len(matches) == 1:
            return _ok(ix, matches[0])
    return _choose_section(ix, candidates)


def gs1_check_digit(digits: str) -> int:
    """GS1 check digit of 17 digits: weights 3,1,3,... from the left."""
    total = sum(
        int(digit) * (3 if position % 2 == 0 else 1) for position, digit in enumerate(digits)
    )
    return (10 - total % 10) % 10


def check_ean(raw: str | None) -> dict[str, Any]:
    """Informative EAN check, never blocking. An invalid format makes every check false/None."""
    ean = trim(raw)
    if not _EAN.fullmatch(ean):
        return {
            "ean": ean,
            "format": False,
            "belgian_prefix": False,
            "gs1_check": None,
            "known_operator": None,
        }
    return {
        "ean": ean,
        "format": True,
        "belgian_prefix": ean.startswith("54"),
        "gs1_check": gs1_check_digit(ean[:17]) == int(ean[17]),
        "known_operator": "sibelga" if ean.startswith(SIBELGA_EAN_PREFIX) else None,
    }


# --- Vectors ------------------------------------------------------------------------------


def observe(ix: Index, vector: Row) -> dict[str, str]:
    """What a vector row observes, in the terms of its expect_* columns."""
    if vector["kind"] == "ean":
        check = check_ean(vector["ean"])
        flags = [
            flag
            for flag, on in (
                ("belgian_prefix", check["belgian_prefix"]),
                ("gs1_ok", check["gs1_check"] is True),
            )
            if on
        ]
        return {
            "status": "format_ok" if check["format"] else "format_invalid",
            "key": "",
            "elec": check["known_operator"] or "",
            "gas_status": "",
            "gas": "",
            "flags": join_list(flags),
        }
    if vector["kind"] == "postcode":
        found = resolve(ix, vector["postcode"], vector["ins"], vector["section_key"])
    else:
        found = resolve_address(ix, vector["ins"], vector["postcode"], vector["post_name"])
    observed = {
        "status": found["status"],
        "key": "",
        "elec": "",
        "gas_status": "",
        "gas": "",
        "flags": "",
    }
    if found["status"] == "ok":
        result = found["result"]
        key = result["ins"] + (f"/{result['section']['section_key']}" if result["section"] else "")
        observed.update(
            key=key,
            elec=result["elec"]["operator"],
            gas_status=result["gas"]["status"],
            gas=result["gas"]["operator"],
            flags=join_list(result["flags"]),
        )
    elif found["status"] == "choose_commune":
        observed["key"] = join_list([commune["ins"] for commune in found["communes"]])
    elif found["status"] == "choose_section":
        observed["key"] = join_list([s["section_key"] for s in found["sections"]])
    return observed


def check_observed(vector: Row, observed: Mapping[str, str]) -> list[str]:
    """Mismatches between a vector and what was observed. Empty cell = must be empty,
    `*` = not asserted; key and flags are compared as sets of their `|`-separated values.

    The comparison half of the reference `check_vector`, split out so the HTTP tests
    apply exactly the same semantics to what the API answered."""
    problems = []
    for field, actual in observed.items():
        expected = vector[f"expect_{field}"]
        if expected == ANY:
            continue
        if field in ("key", "flags"):
            same = set(split_list(expected)) == set(split_list(actual))
        else:
            same = expected == actual
        if not same:
            problems.append(f"{vector['name']}: {field} expected {expected!r}, got {actual!r}")
    return problems


def check_vector(ix: Index, vector: Row) -> list[str]:
    """Mismatches of one vector (see `check_observed`)."""
    return check_observed(vector, observe(ix, vector))


def run_vectors(ix: Index, vectors: Iterable[Row]) -> list[str]:
    """Every mismatch of every vector (an empty list means they all pass)."""
    return [problem for vector in vectors for problem in check_vector(ix, vector)]
