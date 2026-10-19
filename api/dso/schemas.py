"""The HTTP shapes of the dso-lookup API, and the mapping from the reference results.

These are the shapes crm-backend's `src/modules/dso/domain/dso.types.ts` reads,
field for field; every route wraps them as `{"data": <payload>, "error_code": 0}`
(`core.api_response.ApiResponse`). A change here is a change there.

`api.dso.lookup` keeps the reference (belgian-dso-map `lookup.py`) result shape;
`resolve_out` enriches it from the store:
- a commune carries its region and province;
- a section carries its postcodes and its electricity operator (in `choose_section`
  every candidate does, so the app can prefill when they all agree);
- `choose_section` also lists its one commune in `communes`;
- a result carries its note's text instead of the bare `note_key`;
- empty values become null where the type says so (`gas.operator`, `valid_from`);
- empty NL/DE names fall back to FR.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from api.dso.store import Commune, DsoStore, Names, Operator, Section

Region = Literal["WAL", "VLA", "BRU"]
Status = Literal["ok", "choose_commune", "choose_section", "not_found"]


class NamesOut(BaseModel):
    fr: str
    nl: str
    de: str


class CommuneOut(BaseModel):
    ins: str
    names: NamesOut
    region: Region
    province: str


class SectionOut(BaseModel):
    section_key: str
    names: NamesOut
    postcodes: list[str]
    elec_operator: str


class ElecOut(BaseModel):
    operator: str
    designated: str


class GasOut(BaseModel):
    status: Literal["designated", "none", "foreign"]
    operator: str | None


class NoteOut(BaseModel):
    key: str
    fr: str
    nl: str
    de: str
    en: str


class ResultOut(BaseModel):
    ins: str
    commune: NamesOut
    section: SectionOut | None
    region: Region
    regulator: str
    elec: ElecOut
    gas: GasOut
    flags: list[str]
    note: NoteOut | None
    source_ids: list[str]
    valid_from: str | None
    data_version: str


class ResolveOut(BaseModel):
    status: Status
    communes: list[CommuneOut] | None = None
    sections: list[SectionOut] | None = None
    result: ResultOut | None = None
    data_version: str


class OperatorOut(BaseModel):
    id: str
    kind: Literal["grd", "grtl", "gas_foreign"]
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


class EanCheckOut(BaseModel):
    """Informative only: nothing refuses an EAN on these grounds. The EAN itself
    is never echoed back."""

    format: bool
    belgian_prefix: bool
    gs1_check: bool | None
    known_operator: Literal["sibelga"] | None


class CommunesByRegionOut(BaseModel):
    BRU: int
    VLA: int
    WAL: int


class MetaOut(BaseModel):
    data_version: str
    version_date: str
    communes: int
    communes_by_region: CommunesByRegionOut
    rows: int
    operators: int


# --- Mapping -------------------------------------------------------------------------------


def _names_out(names: Names) -> NamesOut:
    return NamesOut(fr=names.fr, nl=names.nl, de=names.de)


def commune_out(commune: Commune) -> CommuneOut:
    return CommuneOut(
        ins=commune.ins,
        names=_names_out(commune.names),
        region=commune.region,  # a Literal: checked against REGIONS at load
        province=commune.province,
    )


def section_out(section: Section) -> SectionOut:
    return SectionOut(
        section_key=section.section_key,
        names=_names_out(section.names),
        postcodes=list(section.postcodes),
        elec_operator=section.elec_operator,
    )


def _result_out(store: DsoStore, result: dict[str, Any]) -> ResultOut:
    ins = result["ins"]
    section = (
        section_out(store.sections[(ins, result["section"]["section_key"])])
        if result["section"]
        else None
    )
    note = store.notes.get(result["note_key"]) if result["note_key"] else None
    return ResultOut(
        ins=ins,
        commune=_names_out(store.communes[ins].names),
        section=section,
        region=result["region"],
        regulator=result["regulator"],
        elec=ElecOut(**result["elec"]),
        gas=GasOut(status=result["gas"]["status"], operator=result["gas"]["operator"] or None),
        flags=list(result["flags"]),
        note=(
            NoteOut(key=note.key, fr=note.fr, nl=note.nl, de=note.de, en=note.en) if note else None
        ),
        source_ids=list(result["source_ids"]),
        valid_from=result["valid_from"] or None,
        data_version=result["data_version"],
    )


def resolve_out(store: DsoStore, found: dict[str, Any]) -> ResolveOut:
    """The HTTP shape of a reference result (`lookup.resolve` / `lookup.resolve_address`)."""
    status = found["status"]
    if status == "choose_commune":
        return ResolveOut(
            status=status,
            communes=[commune_out(store.communes[c["ins"]]) for c in found["communes"]],
            data_version=found["data_version"],
        )
    if status == "choose_section":
        ins = found["ins"]
        return ResolveOut(
            status=status,
            communes=[commune_out(store.communes[ins])],
            sections=[
                section_out(store.sections[(ins, s["section_key"])]) for s in found["sections"]
            ],
            data_version=found["data_version"],
        )
    if status == "ok":
        return ResolveOut(
            status=status,
            result=_result_out(store, found["result"]),
            data_version=found["data_version"],
        )
    return ResolveOut(status="not_found", data_version=found["data_version"])


def operator_out(operator: Operator) -> OperatorOut:
    return OperatorOut(
        id=operator.id,
        kind=operator.kind,  # a Literal: checked against KIND_RANK at load
        name=operator.name,
        legal_name=operator.legal_name,
        region=operator.region,
        elec=operator.elec,
        gas=operator.gas,
        website=operator.website,
        regulator=operator.regulator,
        tariffs_url=operator.tariffs_url,
        lookup=operator.lookup,
        replaced_by=operator.replaced_by,
    )


def meta_out(store: DsoStore) -> MetaOut:
    return MetaOut(
        data_version=store.release.version,
        version_date=store.release.date,
        communes=store.counts["communes"],
        communes_by_region=CommunesByRegionOut(**store.counts["communes_by_region"]),
        rows=store.counts["rows"],
        operators=store.counts["operators"],
    )
