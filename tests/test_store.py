"""The store is strict: a release that breaks the contract fails the boot.

Each case copies data/, breaks ONE rule, re-writes SHA256SUMS (unless the rule
under test is the checksum itself) and expects `DataError` naming the problem.
"""

import csv
import json
import re
import shutil
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest

from api.dso import contract
from api.dso.store import DataError, DsoStore, load_store
from tests.conftest import DATA_DIR, write_sums

Edit = Callable[..., Path]


def _lines(text: str) -> list[str]:
    return text.split("\n")


def _replace_cell(text: str, line_no: int, column: str, value: str) -> str:
    """Set one cell of a simple CSV line (no quoted commas in the edited lines)."""
    lines = _lines(text)
    header = lines[0].split(",")
    cells = lines[line_no].split(",")
    assert len(cells) == len(header), "edit a line without quoted commas"
    cells[header.index(column)] = value
    lines[line_no] = ",".join(cells)
    return "\n".join(lines)


def _first_line(text: str, pattern: str) -> int:
    return next(i for i, line in enumerate(_lines(text)) if re.search(pattern, line))


# --- The real data ----------------------------------------------------------------------------


def test_the_shipped_data_loads(store: DsoStore):
    assert store.counts == store.release.counts
    assert store.index.data_version == store.release.version
    assert store.operators, "no operator"
    assert store.communes, "no commune"


def test_counts_are_the_release_counts(store: DsoStore):
    rows = [row for rows in store.index.by_postcode.values() for row in rows]
    assert store.counts["rows"] == len(rows)
    assert store.counts["communes"] == len(store.communes)
    assert sum(store.counts["communes_by_region"].values()) == store.counts["communes"]


def test_section_postcodes_and_names(store: DsoStore):
    """A section lists every postcode it spans; empty NL/DE names fall back to FR."""
    for (ins, key), section in store.sections.items():
        rows = [row for row in store.index.by_ins[ins] if row["section_key"] == key]
        assert section.postcodes == tuple(sorted({row["postcode"] for row in rows}))
        assert section.names.nl and section.names.de


# --- Integrity (SHA256SUMS rule) ----------------------------------------------------------------


def test_report_md_is_optional_but_must_match_when_present(release_copy: Path):
    (release_copy / "REPORT.md").write_bytes(b"# Report\n")
    with pytest.raises(DataError, match="REPORT.md does not match"):
        load_store(release_copy)
    write_sums(release_copy)
    load_store(release_copy)


def _edit_first_note_key(text: str) -> str:
    """One byte of notes.csv changed, whatever the release holds (a replace of a
    word the data no longer contains would silently change nothing)."""
    header, first, rest = text.split("\n", 2)
    edited = first[0].swapcase() + first[1:]
    assert edited != first, "the first note key does not start with a letter"
    return f"{header}\n{edited}\n{rest}"


def test_a_file_edited_after_the_build_is_refused(edit_release: Edit):
    directory = edit_release("notes.csv", _edit_first_note_key, resum=False)
    with pytest.raises(DataError, match="notes.csv does not match"):
        load_store(directory)


def test_a_missing_required_file_is_refused(release_copy: Path):
    (release_copy / "ATTRIBUTION.md").unlink()
    write_sums(release_copy)
    with pytest.raises(DataError, match=r"missing file\(s\): ATTRIBUTION.md"):
        load_store(release_copy)


def test_an_unlisted_file_is_refused(release_copy: Path):
    sums = (release_copy / "SHA256SUMS").read_text(encoding="utf-8")
    kept = "".join(line + "\n" for line in sums.splitlines() if not line.endswith("  notes.csv"))
    (release_copy / "SHA256SUMS").write_bytes(kept.encode())
    with pytest.raises(DataError, match="notes.csv is not listed"):
        load_store(release_copy)


def test_a_stray_file_is_refused(release_copy: Path):
    (release_copy / "extra.csv").write_bytes(b"a\n")
    with pytest.raises(DataError, match="'extra.csv' is not a file of a version"):
        load_store(release_copy)


def test_a_malformed_sums_file_is_refused(release_copy: Path):
    sums = (release_copy / "SHA256SUMS").read_bytes()
    (release_copy / "SHA256SUMS").write_bytes(sums.replace(b"  ", b" *", 1))
    with pytest.raises(DataError, match="is not `<sha256>  <name>`"):
        load_store(release_copy)


# --- Dialect -------------------------------------------------------------------------------------


def test_a_bom_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="BOM"):
        load_store(edit_release("dso_lookup.csv", lambda t: "\ufeff" + t))


def test_crlf_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="CR"):
        load_store(edit_release("operators.csv", lambda t: t.replace("\n", "\r\n")))


def test_a_semicolon_dialect_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="header is"):
        load_store(edit_release("dso_lookup.csv", lambda t: t.replace(",", ";")))


def test_a_wrong_header_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="header is"):
        load_store(edit_release("dso_lookup.csv", lambda t: t.replace("elec_operator", "elec", 1)))


def test_a_short_row_is_refused(edit_release: Edit):
    def drop_last_cell(text: str) -> str:
        lines = _lines(text)
        lines[1] = lines[1].rsplit(",", 1)[0]
        return "\n".join(lines)

    with pytest.raises(DataError, match="fields, expected"):
        load_store(edit_release("dso_lookup.csv", drop_last_cell))


def test_a_padded_cell_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="leading or trailing whitespace"):
        load_store(
            edit_release("dso_lookup.csv", lambda t: _replace_cell(t, 1, "commune_fr", " X"))
        )


# --- Keys and versions ----------------------------------------------------------------------------


def test_a_duplicate_key_is_refused(edit_release: Edit):
    def duplicate(text: str) -> str:
        lines = _lines(text)
        lines.insert(2, lines[1])
        return "\n".join(lines)

    with pytest.raises(DataError, match="duplicate key"):
        load_store(edit_release("dso_lookup.csv", duplicate))


def test_unsorted_rows_are_refused(edit_release: Edit):
    def swap(text: str) -> str:
        lines = _lines(text)
        lines[1], lines[2] = lines[2], lines[1]
        return "\n".join(lines)

    with pytest.raises(DataError, match="not sorted"):
        load_store(edit_release("dso_lookup.csv", swap))


def test_a_mixed_data_version_is_refused(edit_release: Edit, store: DsoStore):
    version = store.release.version
    with pytest.raises(DataError, match="data_version"):
        load_store(edit_release("sources.csv", lambda t: t.replace(version, "2026.09.0", 1)))


def test_a_release_json_version_mismatch_is_refused(release_copy: Path, store: DsoStore):
    """release.json (and its CHANGES heading) name another version than the rows carry."""
    draft = store.release.version.endswith("-draft")
    content = json.loads((release_copy / "release.json").read_text(encoding="utf-8"))
    content["version"] = "2026.11.0" + ("-draft" if draft else "")
    content["date"] = "2026-11-02"
    (release_copy / "release.json").write_bytes((json.dumps(content, indent=2) + "\n").encode())
    changes = (release_copy / "CHANGES.md").read_text(encoding="utf-8")
    newest = "## 2026.11.0 — 2026-11-02\n- no changes\n\n"
    changes = changes.replace("# Changes\n\n", "# Changes\n\n" + newest)
    (release_copy / "CHANGES.md").write_bytes(changes.encode())
    write_sums(release_copy)
    with pytest.raises(DataError, match="data_version"):
        load_store(release_copy)


# --- Content dates (README "Content dates", validate.py rules 8bis and 9) -----------------------


def _day_after(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _plain_line(name: str) -> int:
    """The first data line of `name` without a quoted cell (`_replace_cell` splits on commas)."""
    lines = _lines((DATA_DIR / name).read_text(encoding="utf-8"))
    return next(i for i, line in enumerate(lines) if i > 0 and line and '"' not in line)


@pytest.mark.parametrize(
    ("name", "column", "value", "message"),
    [
        ("dso_lookup.csv", "changed_at", "AFTER", "changed_at"),
        ("dso_lookup.csv", "first_seen", "", "first_seen"),
        ("operators.csv", "changed_at", "2026-13-01", "changed_at"),
        ("notes.csv", "first_seen", "AFTER", "first_seen"),
        ("sources.csv", "changed_at", "AFTER", "changed_at"),
    ],
)
def test_content_dates_are_dates_on_or_before_the_version_date(
    edit_release: Edit, store: DsoStore, name: str, column: str, value: str, message: str
):
    value = _day_after(store.release.date) if value == "AFTER" else value
    line = _plain_line(name)
    with pytest.raises(DataError, match=message):
        load_store(edit_release(name, lambda t: _replace_cell(t, line, column, value)))


def test_first_seen_is_not_after_changed_at(edit_release: Edit, store: DsoStore):
    earlier = (date.fromisoformat(store.release.date) - timedelta(days=11)).isoformat()

    def reversed_dates(text: str) -> str:
        line = _first_line(text, r"^ores,")
        text = _replace_cell(text, line, "first_seen", store.release.date)
        return _replace_cell(text, line, "changed_at", earlier)

    with pytest.raises(DataError, match="first_seen .* after changed_at"):
        load_store(edit_release("operators.csv", reversed_dates))


def test_valid_from_is_not_after_the_version_date(edit_release: Edit, store: DsoStore):
    later = _day_after(store.release.date)
    with pytest.raises(DataError, match="valid_from"):
        load_store(
            edit_release("dso_lookup.csv", lambda t: _replace_cell(t, 1, "valid_from", later))
        )


@pytest.mark.parametrize("value", ["soon", "", "AFTER"])
def test_a_source_was_retrieved_on_a_date_on_or_before_the_version_date(
    edit_release: Edit, store: DsoStore, value: str
):
    value = _day_after(store.release.date) if value == "AFTER" else value
    line = _plain_line("sources.csv")
    with pytest.raises(DataError, match="retrieved_at"):
        load_store(
            edit_release("sources.csv", lambda t: _replace_cell(t, line, "retrieved_at", value))
        )


def test_release_json_counts_must_be_the_files_counts(edit_release: Edit):
    def miscount(text: str) -> str:
        content = json.loads(text)
        content["counts"]["rows"] += 1
        return json.dumps(content, indent=2) + "\n"

    with pytest.raises(DataError, match="counts"):
        load_store(edit_release("release.json", miscount))


def test_release_json_needs_exactly_its_keys(edit_release: Edit):
    def extra(text: str) -> str:
        content = json.loads(text)
        content["source"] = "local"
        return json.dumps(content) + "\n"

    with pytest.raises(DataError, match="exactly the keys"):
        load_store(edit_release("release.json", extra))


# --- References and vocabularies ------------------------------------------------------------------


def test_an_unknown_operator_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="not in operators.csv"):
        load_store(
            edit_release("dso_lookup.csv", lambda t: _replace_cell(t, 1, "elec_operator", "acme"))
        )


def test_elia_is_never_assigned(edit_release: Edit):
    """`lookup:false`: a lookup never returns Elia; it is a manual choice in the app."""

    def assign_elia(text: str) -> str:
        line = _first_line(text, r",WAL,")
        text = _replace_cell(text, line, "elec_operator", "elia")
        return _replace_cell(text, line, "elec_designated", "elia")

    with pytest.raises(DataError, match="elia"):
        load_store(edit_release("dso_lookup.csv", assign_elia))


def test_lookup_false_is_refused_whatever_the_kind(edit_release: Edit):
    def ores_lookup_false(text: str) -> str:
        line = _first_line(text, r"^ores,")
        return _replace_cell(text, line, "lookup", "false")

    with pytest.raises(DataError, match="lookup:false or replaced"):
        load_store(edit_release("operators.csv", ores_lookup_false))


def test_a_replaced_operator_is_never_assigned(edit_release: Edit):
    def replace_ores(text: str) -> str:
        line = _first_line(text, r"^ores,")
        return _replace_cell(text, line, "replaced_by", "resa")

    with pytest.raises(DataError, match="lookup:false or replaced"):
        load_store(edit_release("operators.csv", replace_ores))


def test_operators_stay_in_display_order(edit_release: Edit):
    """GRDs first (the app's GRD_OPTIONS order), then foreign gas, then Elia last."""

    def elia_first(text: str) -> str:
        lines = _lines(text)
        elia = next(i for i, line in enumerate(lines) if line.startswith("elia,"))
        lines.insert(1, lines.pop(elia))
        return "\n".join(lines)

    with pytest.raises(DataError, match="listed by kind"):
        load_store(edit_release("operators.csv", elia_first))


def test_an_unknown_note_key_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="note_key"):
        load_store(
            edit_release(
                "dso_lookup.csv", lambda t: _replace_cell(t, 1, "note_key", "no-such-note")
            )
        )


def test_an_unknown_source_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="not in sources.csv"):
        load_store(
            edit_release("dso_lookup.csv", lambda t: _replace_cell(t, 1, "source_ids", "nowhere"))
        )


def test_a_bad_flag_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="flags"):
        load_store(edit_release("dso_lookup.csv", lambda t: _replace_cell(t, 1, "flags", "maybe")))


def test_a_bad_region_is_refused(edit_release: Edit):
    with pytest.raises(DataError, match="region"):
        load_store(edit_release("dso_lookup.csv", lambda t: _replace_cell(t, 1, "region", "BXL")))


def test_gas_operator_follows_gas_status(edit_release: Edit):
    with pytest.raises(DataError, match="gas_operator"):
        load_store(
            edit_release("dso_lookup.csv", lambda t: _replace_cell(t, 1, "gas_status", "none"))
        )


# --- Communes and sections ------------------------------------------------------------------------


def test_a_commune_never_mixes_section_rows_and_whole_commune_rows(
    edit_release: Edit, store: DsoStore
):
    ins, key = next(iter(store.sections))

    def unsection(text: str) -> str:
        line = _first_line(text, rf",{ins},.*,{key},")
        for column in ("section_key", "section_fr", "section_nl", "section_de"):
            text = _replace_cell(text, line, column, "")
        return text

    with pytest.raises(DataError, match="mixes section rows"):
        load_store(edit_release("dso_lookup.csv", unsection))


def test_a_section_is_the_same_on_all_its_postcodes(release_copy: Path, store: DsoStore):
    """A section spanning two postcodes has one attribution: give its second postcode
    another `valid_from` and the release is refused."""
    section = next(iter(store.sections.values()))
    path = release_copy / "dso_lookup.csv"
    lines = path.read_text(encoding="utf-8").split("\n")
    at = next(
        i
        for i, line in enumerate(lines)
        if re.match(rf"{section.postcodes[-1]},{section.ins},.*,{section.section_key},", line)
    )
    twin = _replace_cell("\n".join([lines[0], lines[at]]), 1, "valid_from", "2020-01-01")
    # The same section on a postcode just above its last one.
    twin_line = str(int(section.postcodes[-1]) + 1) + twin.split("\n")[1][4:]
    body = [line for line in lines[1:] if line] + [twin_line]
    body.sort(key=lambda line: tuple(next(csv.reader([line]))[i] for i in (0, 1, 7)))
    path.write_bytes(("\n".join([lines[0], *body]) + "\n").encode())
    write_sums(release_copy)
    with pytest.raises(DataError, match="differs between its postcodes"):
        load_store(release_copy)


def test_a_commune_has_one_name(edit_release: Edit, store: DsoStore):
    ins = next(ins for ins, rows in store.index.by_ins.items() if len(rows) > 1)

    def rename(text: str) -> str:
        line = _first_line(text, rf"^[0-9]{{4}},{ins},")
        return _replace_cell(text, line, "commune_nl", "Anders")

    with pytest.raises(DataError, match="different names"):
        load_store(edit_release("dso_lookup.csv", rename))


# --- CHANGES.md -----------------------------------------------------------------------------------


def test_changes_md_grammar(edit_release: Edit):
    with pytest.raises(DataError, match="not a heading or an entry"):
        load_store(
            edit_release("CHANGES.md", lambda t: t + "- removed: 1301,25112, (2026-10-12)\n")
        )


def test_changes_md_heading_is_the_version_served(edit_release: Edit):
    with pytest.raises(DataError, match="newest heading"):
        load_store(edit_release("CHANGES.md", lambda t: re.sub(r"## \S+ —", "## 2026.09.0 —", t)))


def test_changes_parser_matches_the_contract_examples():
    text = (
        "# Changes\n\n"
        "## 2026.11.0 — 2026-11-02\n"
        "- removed dso_lookup 1301|25112| — Wavre\n"
        "- changed dso_lookup 5020|92094|flawinne — Flawinne (Namur); elec_operator: aieg→ores\n"
        "- changed operators enexis — Enexis; website\n"
        "- added notes gesves-exchange\n\n"
        "## 2026.10.0 — 2026-10-12\n"
        "- initial release\n"
    )
    sections = contract.parse_changes(text)
    assert [s.version for s in sections] == ["2026.11.0", "2026.10.0"]
    assert sections[0].date == "2026-11-02"
    for broken in (
        text.replace(" — 2026-11-02", " - 2026-11-02"),  # a hyphen, not U+2014
        text.replace("2026.11.0", "2026.13.0"),  # month 13
        text.replace("## 2026.10.0 — 2026-10-12", "## 2026.12.0 — 2026-10-12"),  # order
    ):
        with pytest.raises(contract.ChangesError):
            contract.parse_changes(broken)


def test_the_data_directory_must_exist(tmp_path: Path):
    with pytest.raises(DataError, match="not a directory"):
        load_store(tmp_path / "nowhere")


def test_a_copy_of_data_loads_unchanged(release_copy: Path, store: DsoStore):
    """The fixtures above start from a copy that is itself valid."""
    assert load_store(release_copy).counts == store.counts
    shutil.rmtree(release_copy)
    assert DATA_DIR.is_dir()
