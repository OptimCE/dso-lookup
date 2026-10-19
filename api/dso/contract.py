"""The belgian-dso-map data contract, as this service reads it.

The contract is belgian-dso-map's README.md; its reference implementation is
`scripts/schema.py` (headers, vocabularies, patterns), `scripts/csvio.py` (the
CSV dialect) and `scripts/changes.py` (the CHANGES.md grammar). This module
ports the parts a CONSUMER needs, without changing their meaning:

- the published headers and vocabularies;
- the CSV dialect, read strictly (a BOM, a CR, a missing final newline, a wrong
  header, a short row, a padded or non-NFC cell is an error);
- the CHANGES.md grammar (`parse_changes`, the reference parser);
- `release.json`;
- the consumer-side integrity rule of SHA256SUMS: every file present (other
  than SHA256SUMS) is listed and matches, the required set is present, and
  REPORT.md is optional. Never a plain `sha256sum -c` on a consumer copy.

Standard library only: `scripts/sync_data.py` runs it on a bare runner.
A change of the contract is a change in three repositories (belgian-dso-map,
the website and this one), made in the same series of pull requests.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Any

Row = dict[str, str]

# --- Published files (a version) --------------------------------------------------------

ATTRIBUTION_COLUMNS = (
    "elec_operator",
    "elec_designated",
    "gas_status",
    "gas_operator",
    "flags",
    "note_key",
    "source_ids",
    "valid_from",
)
DATE_COLUMNS = ("first_seen", "changed_at", "data_version")
DSO_LOOKUP_DATA_COLUMNS = (
    "postcode",
    "ins",
    "commune_fr",
    "commune_nl",
    "commune_de",
    "region",
    "province",
    "section_key",
    "section_fr",
    "section_nl",
    "section_de",
    *ATTRIBUTION_COLUMNS,
)
DSO_LOOKUP_HEADER = (*DSO_LOOKUP_DATA_COLUMNS, *DATE_COLUMNS)
DSO_LOOKUP_KEY = ("postcode", "ins", "section_key")
OPERATORS_HEADER = (
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
)
SOURCES_HEADER = (
    "id",
    "role",
    "publisher",
    "title",
    "url",
    "retrieved_at",
    "sha256",
    "license",
    "note",
)
NOTE_TEXT_COLUMNS = ("fr", "nl", "de", "en")
DIST_OPERATORS_HEADER = (*OPERATORS_HEADER, *DATE_COLUMNS)
DIST_SOURCES_HEADER = (*SOURCES_HEADER, *DATE_COLUMNS)
DIST_NOTES_HEADER = ("key", *NOTE_TEXT_COLUMNS, *DATE_COLUMNS)

VECTORS_HEADER = (
    "name",
    "kind",
    "postcode",
    "ins",
    "section_key",
    "post_name",
    "ean",
    "expect_status",
    "expect_key",
    "expect_elec",
    "expect_gas_status",
    "expect_gas",
    "expect_flags",
)
# Input cells keep their padding: " 5020 " and NBSP+"5020" are test cases of TRIM.
VECTOR_INPUT_COLUMNS = ("postcode", "ins", "section_key", "post_name", "ean")
VECTOR_KINDS = ("postcode", "address", "ean")

# The assets of a version (README "Files of a version"). REPORT.md is optional on
# the consumer side: this service does not keep it.
RELEASE_FILES = (
    "dso_lookup.csv",
    "operators.csv",
    "notes.csv",
    "sources.csv",
    "vectors.csv",
    "CHANGES.md",
    "REPORT.md",
    "release.json",
    "ATTRIBUTION.md",
    "SHA256SUMS",
)
OPTIONAL_FILES = ("REPORT.md",)
REQUIRED_FILES = tuple(name for name in RELEASE_FILES if name not in OPTIONAL_FILES)
SHA256SUMS = "SHA256SUMS"

# --- Vocabularies -----------------------------------------------------------------------

REGIONS = ("BRU", "VLA", "WAL")
# Display order of operators (README): kind first, then region.
KIND_RANK = {"grd": 0, "gas_foreign": 1, "grtl": 2}
REGION_RANK = {"WAL": 0, "BRU": 1, "VLA": 2}
GAS_STATUSES = ("designated", "none", "foreign")
ROLES = ("R", "M", "C", "X")
FLAGS = ("transition", "operator_differs", "to_confirm", "propane_note")
BOOLEANS = ("true", "false")
LOOKUP_STATUSES = ("ok", "choose_commune", "choose_section", "not_found")
EAN_STATUSES = ("format_ok", "format_invalid")
EAN_FLAGS = ("belgian_prefix", "gs1_ok")
PROVINCES = (
    "BE-VAN",
    "BE-VBR",
    "BE-VLI",
    "BE-VOV",
    "BE-VWV",
    "BE-WBR",
    "BE-WHT",
    "BE-WLG",
    "BE-WLX",
    "BE-WNA",
)
RELEASE_COUNT_KEYS = (
    "communes",
    "communes_by_region",
    "postcodes",
    "rows",
    "sections",
    "operators",
    "notes",
    "sources",
)

# --- Patterns (ASCII only: Python's \d also matches full-width digits) ------------------

INS_RE = re.compile(r"[1-9][0-9]{4}")
POSTCODE_RE = re.compile(r"[1-9][0-9]{3}")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
VERSION_RE = re.compile(r"([0-9]{4})\.(0[1-9]|1[0-2])\.(0|[1-9][0-9]*)")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DRAFT_SUFFIX = "-draft"


def is_date(value: str) -> bool:
    """A real calendar date written YYYY-MM-DD."""
    if not DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def version_key(version: str) -> tuple[int, int, int]:
    """(year, month, n) of `YYYY.MM.N` or `YYYY.MM.N-draft`. ValueError if malformed."""
    base = version.removesuffix(DRAFT_SUFFIX)
    match = VERSION_RE.fullmatch(base)
    if not match:
        raise ValueError(f"version {version!r} is not YYYY.MM.N")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_draft(version: str) -> bool:
    return version.endswith(DRAFT_SUFFIX)


def release_order(version: str) -> tuple[int, int, int, int]:
    """Sort key across releases and drafts: a release outranks its own draft."""
    return (*version_key(version), 0 if is_draft(version) else 1)


def split_list(cell: str) -> list[str]:
    """Values of a `|`-separated list cell; an empty cell is the empty list."""
    return cell.split("|") if cell else []


def join_list(values: list[str]) -> str:
    return "|".join(values)


# --- CSV dialect (port of belgian-dso-map scripts/csvio.py, reading side) ---------------

BOM = b"\xef\xbb\xbf"


class DataError(ValueError):
    """A data file breaks the contract. The message names the file, never a request."""


class CsvError(DataError):
    """A file breaks the dialect or its expected header."""


def check_text_bytes(data: bytes, name: str) -> str:
    """Decode a text file: UTF-8, no BOM, no CR, a final newline when not empty."""
    if data.startswith(BOM):
        raise CsvError(f"{name}: starts with a UTF-8 BOM")
    if b"\r" in data:
        raise CsvError(f"{name}: contains a CR (line endings must be LF)")
    if data and not data.endswith(b"\n"):
        raise CsvError(f"{name}: does not end with a newline")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CsvError(f"{name}: not valid UTF-8 ({exc.reason} at byte {exc.start})") from exc


def _cell_problem(value: str, *, raw: bool) -> str | None:
    """Why a cell breaks the hygiene rules, or None.

    `raw` cells (the inputs of vectors.csv) keep their padding on purpose: they
    exercise the TRIM rule of the lookup. They may still not hold control
    characters, nor U+2028/U+2029.
    """
    for char in value:
        category = unicodedata.category(char)
        if category in ("Cc", "Zl", "Zp") or (category == "Cf" and not raw):
            return f"control, format or line separator character U+{ord(char):04X}"
    if raw:
        return None
    if value != value.strip():
        return "leading or trailing whitespace"
    if not unicodedata.is_normalized("NFC", value):
        return "text is not in Unicode NFC form"
    return None


def parse_csv(
    data: bytes,
    *,
    name: str,
    header: Sequence[str],
    raw_columns: Collection[str] = (),
) -> list[Row]:
    """Parse CSV bytes into rows. The header must be exactly `header`."""
    text = check_text_bytes(data, name)
    if not text:
        raise CsvError(f"{name}: empty file (the header row is required)")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        parsed = list(reader)
    except csv.Error as exc:
        raise CsvError(f"{name}: line {reader.line_num}: {exc}") from exc
    found = tuple(parsed[0])
    if found != tuple(header):
        raise CsvError(f"{name}: header is {','.join(found)!r}, expected {','.join(header)!r}")
    rows: list[Row] = []
    for number, cells in enumerate(parsed[1:], start=2):
        if len(cells) != len(found):
            raise CsvError(f"{name}: line {number}: {len(cells)} fields, expected {len(found)}")
        for column, value in zip(found, cells, strict=True):
            problem = _cell_problem(value, raw=column in raw_columns)
            if problem:
                raise CsvError(f"{name}: line {number}, column {column}: {problem}")
        rows.append(dict(zip(found, cells, strict=True)))
    return rows


# --- CHANGES.md (port of belgian-dso-map scripts/changes.py, parsing side) ---------------

CHANGES_TITLE = "# Changes"
HEADING_RE = re.compile(r"## ([0-9]{4}\.[0-9]{2}\.[0-9]+) — ([0-9]{4}-[0-9]{2}-[0-9]{2})")
ENTRY_RE = re.compile(
    r"- (added|changed|removed) (dso_lookup|operators|notes|sources) (\S+)( — .*)?"
)
INITIAL_RELEASE = "- initial release"
NO_CHANGES = "- no changes"


class ChangesError(DataError):
    """CHANGES.md breaks the grammar."""


@dataclass(frozen=True)
class ChangesSection:
    version: str
    date: str
    entries: tuple[str, ...]

    @property
    def heading(self) -> str:
        return f"## {self.version} — {self.date}"


def parse_changes(text: str) -> list[ChangesSection]:
    """Sections newest first. ChangesError on any line outside the grammar."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != CHANGES_TITLE:
        raise ChangesError(f"line 1: expected {CHANGES_TITLE!r}")
    sections: list[ChangesSection] = []
    current: tuple[str, str, list[str]] | None = None
    for number, line in enumerate(lines[1:], start=2):
        heading = HEADING_RE.fullmatch(line)
        if line == "":
            continue
        if heading:
            if current:
                sections.append(ChangesSection(current[0], current[1], tuple(current[2])))
            current = (heading.group(1), heading.group(2), [])
        elif ENTRY_RE.fullmatch(line) or line in (INITIAL_RELEASE, NO_CHANGES):
            if current is None:
                raise ChangesError(f"line {number}: entry before any version heading")
            current[2].append(line)
        else:
            raise ChangesError(f"line {number}: not a heading or an entry: {line!r}")
    if current:
        sections.append(ChangesSection(current[0], current[1], tuple(current[2])))
    for section in sections:
        if not section.entries:
            raise ChangesError(f"{section.heading}: no entries")
        if not is_date(section.date):
            raise ChangesError(f"{section.heading}: invalid date")
        try:
            version_key(section.version)  # the heading regex lets month 13 through
        except ValueError as exc:
            raise ChangesError(f"{section.heading}: {exc}") from exc
    for newer, older in pairwise(sections):
        if version_key(newer.version) <= version_key(older.version):
            raise ChangesError(f"{newer.heading}: versions must be listed newest first")
        if newer.date < older.date:
            raise ChangesError(f"{newer.heading}: dated before {older.version}")
    return sections


def render_section(section: ChangesSection) -> str:
    return "\n".join((section.heading, *section.entries)) + "\n"


# --- release.json --------------------------------------------------------------------------


@dataclass(frozen=True)
class Release:
    """`release.json` of a published version: {version, date, previous, counts}."""

    version: str
    date: str
    previous: str
    counts: dict[str, Any]


def parse_release(data: bytes, name: str = "release.json") -> Release:
    text = check_text_bytes(data, name)
    try:
        content = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataError(f"{name}: not JSON ({exc.msg})") from exc
    if not isinstance(content, dict) or set(content) != {"version", "date", "previous", "counts"}:
        raise DataError(f"{name}: expected exactly the keys version, date, previous, counts")
    version, day, previous, counts = (
        content["version"],
        content["date"],
        content["previous"],
        content["counts"],
    )
    if not isinstance(version, str):
        raise DataError(f"{name}: version is not a string")
    try:
        version_key(version)
    except ValueError as exc:
        raise DataError(f"{name}: {exc}") from exc
    if not isinstance(day, str) or not is_date(day):
        raise DataError(f"{name}: date {day!r} is not YYYY-MM-DD")
    if not isinstance(previous, str):
        raise DataError(f"{name}: previous is not a string")
    if previous:
        try:
            version_key(previous)
        except ValueError as exc:
            raise DataError(f"{name}: previous: {exc}") from exc
    if not isinstance(counts, dict) or set(counts) != set(RELEASE_COUNT_KEYS):
        raise DataError(f"{name}: counts must have exactly {', '.join(RELEASE_COUNT_KEYS)}")
    by_region = counts["communes_by_region"]
    if not isinstance(by_region, dict) or set(by_region) != set(REGIONS):
        raise DataError(f"{name}: counts.communes_by_region must have exactly BRU, VLA, WAL")
    numbers = [counts[key] for key in RELEASE_COUNT_KEYS if key != "communes_by_region"]
    numbers += list(by_region.values())
    if any(type(n) is not int or n < 0 for n in numbers):
        raise DataError(f"{name}: every count is a non-negative integer")
    return Release(version=version, date=day, previous=previous, counts=counts)


# --- SHA256SUMS (the consumer-side rule) -------------------------------------------------

_SUMS_LINE = re.compile(r"([0-9a-f]{64})  (\S+)")


def parse_sha256sums(data: bytes) -> dict[str, str]:
    """`sha256sum` format, one `<64 hex>  <name>` per line, sorted by name, no duplicate."""
    text = check_text_bytes(data, SHA256SUMS)
    sums: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        match = _SUMS_LINE.fullmatch(line)
        if match is None:
            raise DataError(f"{SHA256SUMS}: line {number} is not `<sha256>  <name>`")
        digest, name = match.groups()
        if name in sums:
            raise DataError(f"{SHA256SUMS}: {name} is listed twice")
        if name not in RELEASE_FILES or name == SHA256SUMS:
            raise DataError(f"{SHA256SUMS}: lists {name!r}, which is not a file of a version")
        sums[name] = digest
    if list(sums) != sorted(sums):
        raise DataError(f"{SHA256SUMS}: names are not sorted")
    return sums


def verify_integrity(files: Mapping[str, bytes]) -> None:
    """The consumer-side rule. DataError when it does not hold.

    1. The required set is present (REPORT.md is optional).
    2. Every file present other than SHA256SUMS is listed and matches.
    """
    missing = [name for name in REQUIRED_FILES if name not in files]
    if missing:
        raise DataError(f"missing file(s): {', '.join(missing)}")
    sums = parse_sha256sums(files[SHA256SUMS])
    for name in sorted(files):
        if name == SHA256SUMS:
            continue
        if name not in sums:
            raise DataError(f"{name} is not listed in {SHA256SUMS}")
        if hashlib.sha256(files[name]).hexdigest() != sums[name]:
            raise DataError(f"{name} does not match its {SHA256SUMS} entry")
