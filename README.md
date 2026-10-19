# OptimCE — DSO lookup (internal service)

[![Website](https://img.shields.io/badge/Website-optimce.be-2e7d32.svg)](https://www.optimce.be/en/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Data licence](https://img.shields.io/badge/Data-CC%20BY%204.0-lightgrey.svg)](ATTRIBUTION.md)

Answers one question for the OptimCE platform: **which distribution system operator
(DSO, *gestionnaire de réseau de distribution*, *netbeheerder*) runs the electricity
and gas networks at a Belgian address?** crm-backend asks it while a user fills in a
meter, to prefill the meter's DSO (`meter_data.grd`). The answer is advisory: the
user can always change it, and when this service is down the form simply keeps
whatever the user picks.

The data is not maintained here. It is a pinned release of
[`belgian-dso-map`](https://github.com/OptimCE/belgian-dso-map), the single source
of truth for the commune → DSO attributions, shipped inside the image in `data/`.

This service is part of the [OptimCE](https://www.optimce.be/en/) platform. The full
stack runs from the [monorepo](https://github.com/OptimCE/monorepo).

## Internal, not an annex

dso-lookup is built on the conventions of the annex services (billing's FastAPI
skeleton, logging, telemetry and CI), but it is **not an annex**:

- **crm-backend is its only caller**, over the Docker network: the `backend` network
  in development (plus a loopback-only port, `127.0.0.1:8097`, for curl checks), and a
  dedicated `dso-lookup` network in production whose only other member is
  crm-backend.
- No KrakenD route, no nginx location, no `*-doc-gen` container, no
  `krakend-builder.yaml` entry. The browser reaches crm-backend's own
  `GET /dso/operators` and `GET /dso/resolve`, which call this service.
- **Never list it in `ANNEX_CATALOG_*`**: crm-backend exits at boot on an annex name
  it does not know.
- No authentication, no CORS, no database, no broker.

## API

Every body is wrapped as `{"data": <payload>, "error_code": 0}`; crm-backend's
`src/modules/dso/infra/dso-lookup.client.ts` unwraps it and reads the types of
`src/modules/dso/domain/dso.types.ts`, field for field.

| Route | Answer |
|---|---|
| `GET /dso/resolve?postcode=&ins=&section=` | `ResolveOut`: by postcode, optionally narrowed by commune (`ins`) then section |
| `GET /dso/resolve-address?nis_code=&postcode=&post_name=` | `ResolveOut`: by the address the BeST picker returned (the 5-digit NIS code, TRIMmed and compared exactly; every INS is `[1-9][0-9]{4}`, so its int form is the same string, and any other value is `not_found`, never a 422) |
| `GET /dso/operators` | `OperatorOut[]`: the catalogue, DSOs first in the app's `GRD_OPTIONS` order, Elia last |
| `GET /dso/ean/{ean}/check` | `{format, belgian_prefix, gs1_check, known_operator}`: informative only, never echoes the EAN |
| `GET /dso/meta` | `{data_version, version_date, communes, communes_by_region: {BRU, VLA, WAL}, rows, operators}` |
| `GET /health/liveness` | `{"status": "alive"}` |
| `GET /health/readiness` | 200 or 503, `{"status", "checks": {"data": …}}` |
| `GET /healthz` | Alias of `/health/readiness` (the name the spec uses) |

`ResolveOut` is `{status, communes?, sections?, result?, data_version}` with
`status` one of:

- `ok`: `result` = `{ins, commune: {fr, nl, de}, section | null, region, regulator,
  elec: {operator, designated}, gas: {status, operator | null}, flags, note | null,
  source_ids, valid_from | null, data_version}`. `elec.operator` is the DSO that runs
  the network (the one on the bill and the one the meter form prefills);
  `elec.designated` is the legally designated one, shown as a note when it differs.
- `choose_commune`: the postcode spans several communes (1040, 1050, 1804);
  `communes` lists them, each `{ins, names, region, province}`.
- `choose_section`: the commune has sections with different operators; `communes`
  holds that one commune and `sections` its candidates, each `{section_key, names,
  postcodes, elec_operator}` (so the caller can prefill when they all agree, as in
  Baarle-Hertog).
- `not_found`: no such postcode or NIS code. **A malformed input is an answer, never
  a 422.**

The algorithm is belgian-dso-map's `scripts/lookup.py`, ported line for line in
`api/dso/lookup.py` (normalisation: TRIM of exactly `[ \t\r\n\u00a0\ufeff]`, postcode
`^[0-9]{4}$`, names NFKD without marks, lowercased, `[a-z0-9]` only; a filter that
matches nothing is ignored; a postal name equal to the commune's name never picks a
section). The regulator comes from the region: `BRU` → `BE-BRU-BRUGEL`, `VLA` →
`BE-VLA-VREG`, `WAL` → `BE-WAL-CWAPE` (the codes of the monorepo's
`reference/regulators.json`, whose `active` flags change nothing here).

## Health

`/health/readiness` (and `/healthz`) is 200 only when:

1. the data is loaded (a `data/` that fails its checks fails the **boot**, so the
   container never runs half-working);
2. the counts recounted from the files equal `data/release.json`'s `counts` and the
   version served is `release.json`'s;
3. for a release (no `-draft`) that is not a fixture (`DSO_DATA_FIXTURE`, refused in
   production): the national counts, 565 communes = 261 WAL / 285 VLA / 19 BRU.

The expected counts come from the data itself, so the mini fixture (6 communes) is
ready too; the national assertion is the extra guard for a real release. The compose
healthchecks (dev and production) probe `/health/readiness` with Python's urllib:
the image has no curl.

## Data

`data/` is a belgian-dso-map release, byte for byte (`.gitattributes`:
`data/** -text`), minus its optional `REPORT.md`. **Only `scripts/sync_data.py`
writes it**, and it rewrites `ATTRIBUTION.md` from the release's at the same time:

```bash
python scripts/sync_data.py --latest --if-newer            # what the bump runs; no token needed (DSO_MAP_TOKEN optional)
python scripts/sync_data.py --version 2026.10.0            # a published release; naming it may roll data/ back
python scripts/sync_data.py --latest --check               # exit 10 when a newer release exists, writes nothing
python scripts/sync_data.py --from-dir ../../belgian-dso-map/dist [--dry-run]
python scripts/sync_data.py --from-dir ../../belgian-dso-map/.build/mini --allow-draft
```

The flags and exit codes are those of the website's `scripts/sync_dso.py` (`--version`
| `--latest`, `--from-dir`, `--allow-draft`, `--if-newer`, `--dry-run`, `--pr-body`,
`--github-output`; 0 synced or up to date, 1 refused or failed, 2 usage), plus
`--check` (exit 10) and `--data-dir`. `--latest` reads GitHub and is refused with
`--from-dir`; a release GitHub marks `draft` or `prerelease` is refused; only
`/repos/{repo}/releases/assets/…` URLs are fetched, and redirects only over https.
belgian-dso-map is public, so GitHub is read **anonymously** when `DSO_MAP_TOKEN` is
unset or empty (no `Authorization` header at all). Set it to any GitHub token only to
lift the anonymous API rate limit (60 requests an hour per IP; a sync makes about
ten): it is sent as an unredirected header, never to the signed storage URL the
assets redirect to, and never printed. No other variable (`GITHUB_TOKEN`, `GH_TOKEN`)
is read.
One difference, on purpose: a release **older** than `data/`'s is refused unless it
is named with `--version` (a rollback), where `sync_dso.py` proceeds with a note.

Before anything is written, the release is checked exactly as the service checks it
at boot: every file present is listed in `SHA256SUMS` and matches, the required set
is present, the CSV dialect and headers are the contract's, one `data_version`
equals `release.json`, the content dates hold (`first_seen` ≤ `changed_at` ≤ the
version date on every row; `valid_from` and each source's `retrieved_at` on or
before it), the `CHANGES.md` grammar holds, every referenced operator,
note and source exists, no row is assigned a `lookup:false` operator (Elia), and the
counts match. `data/` is then replaced all or nothing. A draft (`-draft`) is refused
unless `--from-dir … --allow-draft`, and CI refuses draft data
(`tests/test_ci_guard.py`, and `build.yml` before it builds an image).

The **data bump** (`.github/workflows/dso-data-bump.yml`) runs `sync_data.py
--latest --if-newer` (or `--version <tag> --if-newer`) and opens the pull request
"dso data X → Y" with a personal access token (`DSO_DATA_BOT_TOKEN`: Contents and
Pull requests read/write on this repository only), because a pull request opened with
`GITHUB_TOKEN` triggers no workflow and the new release's vectors would run nowhere.
Reading the release needs no secret: the sync step gets the run's own `github.token`
as `DSO_MAP_TOKEN`, only against the anonymous rate limit. Merging it publishes a new
`:main-prod`: `build.yml` watches `data/**`. It starts three ways:

- `repository_dispatch` `dso-data-released`, sent by belgian-dso-map's `release.yml`
  with `client_payload.version` when its `DSO_CONSUMERS_TOKEN` secret is set. GitHub
  **disables the cron of a public repository after 60 days without activity** (an
  email, no red check), and a release comes about every six months: the dispatch is
  what keeps the bump alive. When `release.yml` could not send it (no secret, or a
  failed dispatch: its notice or warning says so), run the workflow by hand after the
  release (re-enable it first if GitHub disabled it).
- The cron, Mondays 06:00 UTC: the safety net while it is enabled.
- By hand (`workflow_dispatch`); a `version` given there is pinned even if older.

## Privacy

A lookup carries a postcode, a NIS code, a postal name and an EAN — a home. The
service keeps none of it:

- uvicorn runs with `--no-access-log` in both Dockerfiles (its access line is the
  full query string, and in production the root logger exports to OTLP);
- no route logs a parameter. billing's shared error paths (413, 504, domain and
  unhandled errors) log the URL path, which for `/dso/ean/{ean}/check` holds the
  EAN: `main.py` rewrites it to the route template with a filter on those loggers,
  before any handler sees the record;
- the only trace is the counter `dso.lookups.total{endpoint, status}`, whose labels
  are closed vocabularies (`core/metrics.py` refuses anything else);
- the HTTP metrics of the OpenTelemetry instrumentation carry the route template,
  never the path or the query string; no spans are recorded (no `TracerProvider`
  exists in the platform), and none must be before `/dso/` is excluded from them;
- the EAN check never echoes the EAN; the NIS code is never stored, by anyone.

`tests/test_privacy.py` holds all of this.

## Configuration

| Variable | Default | |
|---|---|---|
| `ENV` | `local` | `local`, `test`, `staging`, `production`. Docs (`/docs`) only in `local` |
| `DSO_DATA_DIR` | `<repo>/data` | The release directory. Leave empty: the image ships it |
| `DSO_DATA_FIXTURE` | `false` | `true` only to serve a non-draft fixture; refused in production |
| `LOGGING_TOKEN`, `LOGGING_LOGS_URL`, `LOGGING_METRICS_URL` | empty | Required in production (logs and metrics over OTLP/HTTP) |
| `LOGGING_TRACES_URL` | empty | Declared for parity with the annexes; unused |
| `DEFAULT_LOCALE` | `fr-BE` | |

## Development

Python 3.12 (`py -3.12` on Windows).

```bash
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements/all.txt

ENV=test .venv/Scripts/python.exe -m pytest -q     # no Docker needed: no database
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy .

.venv/Scripts/uvicorn main:app --reload --no-access-log   # http://127.0.0.1:8000/docs
docker build -f Dockerfile.production -t dso-lookup:local .
```

The tests run every row of `data/vectors.csv` through HTTP, check the store's
strictness on broken copies of `data/`, compare the port with belgian-dso-map's
`scripts/lookup.py` when that repository is checked out next to the monorepo, and
exercise `sync_data.py` (success, refusal, untouched `data/` on failure, no silent
downgrade, `--if-newer`, `--dry-run`, the GitHub path with a fake API).

CI: `lint.yml` (ruff, format, mypy), `test.yml` (pytest, no service container),
`build.yml` (`Dockerfile.production` → `ghcr.io/optimce/dso-lookup:main-prod`, labelled
`optimce.dso.data-version`), `dso-data-bump.yml`, `notify_monorepo_update.yml`.

## Spec deviations

- The spec's `/healthz` is billing's `/health/readiness`; `/healthz` stays as an alias.
- `gh release download` is the GitHub REST API (`urllib`, `add_unredirected_header`)
  plus `--from-dir`: `gh` is not installed on the developers' machines.

## Licence

The code is licensed under the [Apache License 2.0](LICENSE), which does not cover
`data/`. The data in `data/` are a release of the public
[belgian-dso-map](https://github.com/OptimCE/belgian-dso-map) dataset, compiled and
**licensed by OptimCE under the
[Creative Commons Attribution 4.0 International licence (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)**.
To reuse them, credit "belgian-dso-map, OptimCE" with the version used and its date
and a link to the repository, and say whether you changed them. The sources keep their
own licences and attribution requirements: [ATTRIBUTION.md](ATTRIBUTION.md) credits
them, and is rewritten from each data release by `scripts/sync_data.py`.
