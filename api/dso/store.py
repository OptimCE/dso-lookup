"""The data release, loaded once at boot and read in memory by every request.

`load_store(data_dir)` reads the pinned belgian-dso-map release in `data/` and
refuses anything that breaks the contract. It raises `DataError`, which
`main.py`'s lifespan lets through, so a bad image never boots, let alone
answers: the data ships INSIDE the image, and a wrong DSO prefilled in a meter
form costs more than no prefill at all (crm-backend falls back to "the user
picks").

What is checked, in order:

1. Integrity (the consumer-side rule): the required files are present; every
   file present other than SHA256SUMS is listed and matches; REPORT.md is
   optional; nothing else lives in the directory.
2. `release.json` and `CHANGES.md` follow the contract; the newest CHANGES
   heading is the version being served (without `-draft`) and its date.
3. Every CSV file is read in the strict dialect (no BOM, no CR, exact header,
   NFC, no padding) with unique keys, sorted where the contract sorts them.
4. One `data_version` on every row of every file, equal to `release.json`;
   content dates as the contract has them (README "Content dates", validate.py
   rules 8bis and 9): `first_seen` <= `changed_at` <= the version date on every
   row, a `valid_from` and every source's `retrieved_at` on or before it.
5. Every referenced operator, note and source exists; no row is assigned an
   operator that is `lookup:false` (Elia) or replaced; vocabularies hold.
6. The counts of `release.json` are the counts of the files.

Standard library only: `scripts/sync_data.py` runs this same loader on a
downloaded release before it replaces `data/`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from api.dso import contract
from api.dso.contract import DataError, Row
from api.dso.lookup import REGION_REGULATOR, Index, build_index

__all__ = [
    "Commune",
    "DataError",
    "DsoStore",
    "Names",
    "Note",
    "Operator",
    "Section",
    "load_store",
    "read_release_files",
]


@dataclass(frozen=True, slots=True)
class Names:
    fr: str
    nl: str
    de: str


@dataclass(frozen=True, slots=True)
class Commune:
    ins: str
    names: Names
    region: str
    province: str


@dataclass(frozen=True, slots=True)
class Section:
    ins: str
    section_key: str
    names: Names
    postcodes: tuple[str, ...]
    elec_operator: str


@dataclass(frozen=True, slots=True)
class Operator:
    id: str
    kind: str
    name: str
    legal_name: str
    region: str
    elec: bool
    gas: bool
    website: str
    regulator: str
    tariffs_url: str
    lookup: bool
    replaced_by: str | None


@dataclass(frozen=True, slots=True)
class Note:
    key: str
    fr: str
    nl: str
    de: str
    en: str


@dataclass(frozen=True, slots=True)
class DsoStore:
    """Everything a request reads. Immutable once loaded."""

    release: contract.Release
    index: Index
    communes: dict[str, Commune]
    sections: dict[tuple[str, str], Section]
    operators: tuple[Operator, ...]  # display order: the file order
    notes: dict[str, Note]
    source_ids: frozenset[str]
    counts: dict[str, Any]  # recounted from the files, same shape as release.counts

    @property
    def data_version(self) -> str:
        return self.release.version

    @property
    def is_draft(self) -> bool:
        return contract.is_draft(self.release.version)


def _names(row: Row, prefix: str) -> Names:
    """Empty NL/DE names fall back to FR (contract, "Results")."""
    fr = row[f"{prefix}_fr"]
    return Names(fr=fr, nl=row[f"{prefix}_nl"] or fr, de=row[f"{prefix}_de"] or fr)


def _boolean(value: str, where: str) -> bool:
    if value not in contract.BOOLEANS:
        raise DataError(f"{where}: {value!r} is not true or false")
    return value == "true"


def _require_sorted_unique(keys: list[tuple[str, ...]], name: str) -> None:
    for previous, current in pairwise(keys):
        if current == previous:
            raise DataError(f"{name}: duplicate key {'|'.join(current)}")
        if current < previous:
            raise DataError(f"{name}: not sorted by key at {'|'.join(current)}")


def _require_version(rows: list[Row], name: str, version: str) -> None:
    for number, row in enumerate(rows, start=2):
        if row["data_version"] != version:
            raise DataError(
                f"{name}: line {number}: data_version {row['data_version']!r}, "
                f"release.json says {version!r}"
            )


def _require_date(value: str, where: str, column: str, day: str) -> None:
    """`value` is a calendar date on or before `day` (the version date)."""
    if not contract.is_date(value):
        raise DataError(f"{where}: {column} {value!r} is not a date")
    if value > day:  # ISO dates compare as strings
        raise DataError(f"{where}: {column} {value} is after the version date {day}")


def _require_content_dates(rows: list[Row], name: str, day: str) -> None:
    """first_seen <= changed_at <= the version date, both filled (rule 8bis)."""
    for number, row in enumerate(rows, start=2):
        where = f"{name}: line {number}"
        _require_date(row["first_seen"], where, "first_seen", day)
        _require_date(row["changed_at"], where, "changed_at", day)
        if row["first_seen"] > row["changed_at"]:
            raise DataError(
                f"{where}: first_seen {row['first_seen']} is after changed_at {row['changed_at']}"
            )


# --- File reading ---------------------------------------------------------------------------


def read_release_files(data_dir: Path) -> dict[str, bytes]:
    """The bytes of every file in `data_dir`. Anything but regular release files is refused."""
    if not data_dir.is_dir():
        raise DataError(f"{data_dir}: not a directory")
    files: dict[str, bytes] = {}
    for path in sorted(data_dir.iterdir()):
        if not path.is_file() or path.name not in contract.RELEASE_FILES:
            raise DataError(f"{data_dir}: {path.name!r} is not a file of a version")
        files[path.name] = path.read_bytes()
    return files


# --- The tables ------------------------------------------------------------------------------


def _load_operators(rows: list[Row]) -> tuple[Operator, ...]:
    name = "operators.csv"
    operators: list[Operator] = []
    seen: set[str] = set()
    previous_rank = (-1, -1)
    for number, row in enumerate(rows, start=2):
        where = f"{name}: line {number}"
        if not contract.SLUG_RE.fullmatch(row["id"]):
            raise DataError(f"{where}: id {row['id']!r} is not a lowercase slug")
        if row["id"] in seen:
            raise DataError(f"{where}: duplicate id {row['id']!r}")
        seen.add(row["id"])
        if row["kind"] not in contract.KIND_RANK or row["region"] not in contract.REGION_RANK:
            raise DataError(f"{where}: kind {row['kind']!r} or region {row['region']!r}")
        # The file order is the display order: grd (the app's GRD_OPTIONS order),
        # then gas_foreign, then grtl (Elia last), each grouped by region.
        rank = (contract.KIND_RANK[row["kind"]], contract.REGION_RANK[row["region"]])
        if rank < previous_rank:
            raise DataError(
                f"{where}: operators are listed by kind (grd, gas_foreign, grtl), "
                "then region (WAL, BRU, VLA)"
            )
        previous_rank = max(previous_rank, rank)
        if not row["name"] or not row["legal_name"]:
            raise DataError(f"{where}: name and legal_name are required")
        if row["regulator"] not in REGION_REGULATOR.values():
            raise DataError(f"{where}: unknown regulator {row['regulator']!r}")
        operators.append(
            Operator(
                id=row["id"],
                kind=row["kind"],
                name=row["name"],
                legal_name=row["legal_name"],
                region=row["region"],
                elec=_boolean(row["elec"], f"{where}, elec"),
                gas=_boolean(row["gas"], f"{where}, gas"),
                website=row["website"],
                regulator=row["regulator"],
                tariffs_url=row["tariffs_url"],
                lookup=_boolean(row["lookup"], f"{where}, lookup"),
                replaced_by=row["replaced_by"] or None,
            )
        )
    for operator in operators:
        if operator.replaced_by is not None and operator.replaced_by not in seen:
            raise DataError(f"{name}: {operator.id} is replaced by unknown {operator.replaced_by}")
    return tuple(operators)


def _load_notes(rows: list[Row]) -> dict[str, Note]:
    name = "notes.csv"
    _require_sorted_unique([(row["key"],) for row in rows], name)
    notes: dict[str, Note] = {}
    for number, row in enumerate(rows, start=2):
        if not contract.SLUG_RE.fullmatch(row["key"]):
            raise DataError(f"{name}: line {number}: key {row['key']!r} is not a slug")
        if not row["fr"]:
            raise DataError(f"{name}: line {number}: the FR text is required")
        notes[row["key"]] = Note(
            key=row["key"], fr=row["fr"], nl=row["nl"], de=row["de"], en=row["en"]
        )
    return notes


def _load_sources(rows: list[Row]) -> frozenset[str]:
    name = "sources.csv"
    _require_sorted_unique([(row["id"],) for row in rows], name)
    for number, row in enumerate(rows, start=2):
        if row["role"] not in contract.ROLES:
            raise DataError(f"{name}: line {number}: role {row['role']!r}")
    return frozenset(row["id"] for row in rows)


def _check_lookup_rows(
    rows: list[Row],
    operators: dict[str, Operator],
    notes: dict[str, Note],
    source_ids: frozenset[str],
) -> None:
    name = "dso_lookup.csv"
    _require_sorted_unique([tuple(row[c] for c in contract.DSO_LOOKUP_KEY) for row in rows], name)
    for number, row in enumerate(rows, start=2):
        where = f"{name}: line {number}"
        if not contract.POSTCODE_RE.fullmatch(row["postcode"]):
            raise DataError(f"{where}: postcode {row['postcode']!r}")
        if not contract.INS_RE.fullmatch(row["ins"]):
            raise DataError(f"{where}: ins {row['ins']!r}")
        if row["region"] not in contract.REGIONS:
            raise DataError(f"{where}: region {row['region']!r}")
        if (row["province"] == "") != (row["region"] == "BRU") or (
            row["province"] and row["province"] not in contract.PROVINCES
        ):
            raise DataError(f"{where}: province {row['province']!r} in region {row['region']}")
        if not row["commune_fr"]:
            raise DataError(f"{where}: commune_fr is required")
        if row["section_key"]:
            if not contract.SLUG_RE.fullmatch(row["section_key"]) or not row["section_fr"]:
                raise DataError(f"{where}: section {row['section_key']!r} needs a slug and a name")
        elif row["section_fr"] or row["section_nl"] or row["section_de"]:
            raise DataError(f"{where}: section names without a section_key")
        for column in ("elec_operator", "elec_designated"):
            operator = operators.get(row[column])
            if operator is None:
                raise DataError(f"{where}: {column} {row[column]!r} is not in operators.csv")
            if operator.kind != "grd" or not operator.elec:
                raise DataError(f"{where}: {column} {operator.id} is not an electricity DSO")
        if row["gas_status"] not in contract.GAS_STATUSES:
            raise DataError(f"{where}: gas_status {row['gas_status']!r}")
        if (row["gas_status"] == "none") != (row["gas_operator"] == ""):
            raise DataError(f"{where}: gas_operator set ⇔ gas_status designated or foreign")
        if row["gas_operator"]:
            operator = operators.get(row["gas_operator"])
            if operator is None:
                raise DataError(f"{where}: gas_operator {row['gas_operator']!r} is unknown")
            if not operator.gas or (row["gas_status"] == "designated" and operator.kind != "grd"):
                raise DataError(f"{where}: gas_operator {operator.id} is not a gas DSO")
        for column in ("elec_operator", "elec_designated", "gas_operator"):
            operator = operators.get(row[column])
            if operator is not None and (not operator.lookup or operator.replaced_by):
                raise DataError(
                    f"{where}: {column} {operator.id} is lookup:false or replaced; "
                    "a lookup never returns it"
                )
        flags = contract.split_list(row["flags"])
        if any(flag not in contract.FLAGS for flag in flags) or len(set(flags)) != len(flags):
            raise DataError(f"{where}: flags {row['flags']!r}")
        if row["note_key"] and row["note_key"] not in notes:
            raise DataError(f"{where}: note_key {row['note_key']!r} is not in notes.csv")
        unknown = [s for s in contract.split_list(row["source_ids"]) if s not in source_ids]
        if unknown:
            raise DataError(f"{where}: source(s) {', '.join(unknown)} not in sources.csv")


def _communes_and_sections(
    rows: list[Row],
) -> tuple[dict[str, Commune], dict[tuple[str, str], Section]]:
    """One Commune per INS and one Section per (INS, section_key), each consistent
    across its rows; a commune is sectioned on every row or on none."""
    name = "dso_lookup.csv"
    communes: dict[str, Commune] = {}
    sectioned: dict[str, bool] = {}
    section_rows: dict[tuple[str, str], list[Row]] = {}
    for row in rows:
        commune = Commune(
            ins=row["ins"],
            names=_names(row, "commune"),
            region=row["region"],
            province=row["province"],
        )
        if communes.setdefault(row["ins"], commune) != commune:
            raise DataError(f"{name}: INS {row['ins']} has different names or region on its rows")
        has_section = bool(row["section_key"])
        if sectioned.setdefault(row["ins"], has_section) != has_section:
            raise DataError(f"{name}: INS {row['ins']} mixes section rows with whole-commune rows")
        if has_section:
            section_rows.setdefault((row["ins"], row["section_key"]), []).append(row)
    sections: dict[tuple[str, str], Section] = {}
    same_section = ("section_fr", "section_nl", "section_de", *contract.ATTRIBUTION_COLUMNS)
    for (ins, key), group in section_rows.items():
        first = group[0]
        if any(row[c] != first[c] for row in group for c in same_section):
            raise DataError(f"{name}: section {ins}/{key} differs between its postcodes")
        sections[(ins, key)] = Section(
            ins=ins,
            section_key=key,
            names=_names(first, "section"),
            postcodes=tuple(sorted({row["postcode"] for row in group})),
            elec_operator=first["elec_operator"],
        )
    return communes, sections


def _counts(
    rows: list[Row], operators: int, notes: int, sources: int, communes: dict[str, Commune]
) -> dict[str, Any]:
    """The counts of release.json, recounted (belgian-dso-map build.py `counts`)."""
    by_region = Counter(commune.region for commune in communes.values())
    return {
        "communes": len(communes),
        "communes_by_region": {region: by_region[region] for region in contract.REGIONS},
        "postcodes": len({row["postcode"] for row in rows}),
        "rows": len(rows),
        "sections": len({(row["ins"], row["section_key"]) for row in rows if row["section_key"]}),
        "operators": operators,
        "notes": notes,
        "sources": sources,
    }


# --- Loader ----------------------------------------------------------------------------------


def load_files(files: dict[str, bytes]) -> DsoStore:
    """Build the store from the bytes of a release. DataError on any breach of the contract."""
    contract.verify_integrity(files)
    release = contract.parse_release(files["release.json"])
    version = release.version

    sections_of_changes = contract.parse_changes(
        contract.check_text_bytes(files["CHANGES.md"], "CHANGES.md")
    )
    if not sections_of_changes:
        raise DataError("CHANGES.md: no version heading")
    newest = sections_of_changes[0]
    if newest.version != version.removesuffix(contract.DRAFT_SUFFIX) or newest.date != release.date:
        raise DataError(
            f"CHANGES.md: newest heading is {newest.heading!r}, "
            f"release.json says {version} of {release.date}"
        )
    contract.check_text_bytes(files["ATTRIBUTION.md"], "ATTRIBUTION.md")

    lookup_rows = contract.parse_csv(
        files["dso_lookup.csv"], name="dso_lookup.csv", header=contract.DSO_LOOKUP_HEADER
    )
    operator_rows = contract.parse_csv(
        files["operators.csv"], name="operators.csv", header=contract.DIST_OPERATORS_HEADER
    )
    note_rows = contract.parse_csv(
        files["notes.csv"], name="notes.csv", header=contract.DIST_NOTES_HEADER
    )
    source_rows = contract.parse_csv(
        files["sources.csv"], name="sources.csv", header=contract.DIST_SOURCES_HEADER
    )
    # Not served, but part of the release: a vectors.csv this service cannot read
    # is a release the tests cannot hold to the contract.
    contract.parse_csv(
        files["vectors.csv"],
        name="vectors.csv",
        header=contract.VECTORS_HEADER,
        raw_columns=contract.VECTOR_INPUT_COLUMNS,
    )

    tables = {
        "dso_lookup.csv": lookup_rows,
        "operators.csv": operator_rows,
        "notes.csv": note_rows,
        "sources.csv": source_rows,
    }
    for name, rows in tables.items():
        _require_version(rows, name, version)
        _require_content_dates(rows, name, release.date)
    for number, row in enumerate(lookup_rows, start=2):
        if row["valid_from"]:  # empty: no transition date
            _require_date(
                row["valid_from"], f"dso_lookup.csv: line {number}", "valid_from", release.date
            )
    for number, row in enumerate(source_rows, start=2):  # rule 9: never after the version
        _require_date(
            row["retrieved_at"], f"sources.csv: line {number}", "retrieved_at", release.date
        )
    if not lookup_rows:
        raise DataError("dso_lookup.csv: no rows")

    operators = _load_operators(operator_rows)
    operators_by_id = {operator.id: operator for operator in operators}
    notes = _load_notes(note_rows)
    source_ids = _load_sources(source_rows)
    _check_lookup_rows(lookup_rows, operators_by_id, notes, source_ids)
    communes, sections = _communes_and_sections(lookup_rows)

    counts = _counts(lookup_rows, len(operators), len(notes), len(source_ids), communes)
    if counts != release.counts:
        raise DataError(f"release.json: counts {release.counts} but the files hold {counts}")

    index = build_index(lookup_rows)
    if index.data_version != version:
        raise DataError(f"dso_lookup.csv: data_version {index.data_version!r} != {version!r}")
    return DsoStore(
        release=release,
        index=index,
        communes=communes,
        sections=sections,
        operators=operators,
        notes=notes,
        source_ids=source_ids,
        counts=counts,
    )


def load_store(data_dir: Path) -> DsoStore:
    """Read and check a release directory (the repository's `data/`)."""
    return load_files(read_release_files(data_dir))
