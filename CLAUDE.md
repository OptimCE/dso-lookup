# dso-lookup — working notes for Claude

Internal FastAPI service: which DSO runs the electricity and gas networks at a
Belgian address. crm-backend is its only caller (meter-form GRD prefill). Built on
billing's conventions (core/ is copied from billing). Read `README.md` for the API;
the data contract is belgian-dso-map's `README.md`.

## Never

- **Never an annex.** Never list `dso-lookup` in `ANNEX_CATALOG_*` (crm-backend exits
  at boot on an unknown annex name), never add a KrakenD route, an nginx location, a
  `*-doc-gen` container, a `krakend-builder.yaml` entry or `update-documentation.yml`.
- **Never edit `data/` (or `ATTRIBUTION.md`) by hand.** Only `scripts/sync_data.py`
  writes them. `data/` is a belgian-dso-map release byte for byte; its SHA256SUMS
  rule is checked at boot and by `tests/test_data_integrity.py`.
- **Never let a draft reach `main`.** Develop on `--from-dir … --allow-draft`
  (`.build/mini`, then `.build/draft`); CI and `build.yml` refuse `-draft` data.
- **Never log a request parameter** (postcode, NIS code, post_name, EAN), never add a
  metric label that is not a closed vocabulary, never remove `--no-access-log` from
  either Dockerfile, never echo the EAN. The EAN is IN the path of its route: a
  module that logs `request.url.path` goes into `main.py`'s `REDACTED_LOGGERS`
  (`tests/test_privacy.py` scans `core/` for it). Spans are non-recording today;
  whoever adds a `TracerProvider` must exclude `/dso/` from them first.
- **Never `from __future__ import annotations` in `api/dso/routes.py`**:
  `with_default_error` resolves string annotations against its own module, so FastAPI
  would lose the dependency types.
- Never change `api/dso/lookup.py` on its own. It is a line-for-line port of
  belgian-dso-map `scripts/lookup.py`; a change there is a change in three
  repositories (the website ports it too). `tests/test_lookup_reference.py` compares
  the two when belgian-dso-map is checked out next to the monorepo.
- Never copy anything from `template_fastapi_microservice` (stale telemetry bugs).
  Copied billing files stay identical to billing except `service.name` in
  `core/tracing.py`, so fleet-wide fixes stay mechanical.
- Never commit, push, tag or open a pull request: hand over commit-message files and
  a PR description; Eric commits.

## Layout

- `api/dso/contract.py` — the contract a consumer needs: headers, vocabularies, CSV
  dialect, CHANGES.md grammar, `release.json`, the consumer-side SHA256SUMS rule.
- `api/dso/store.py` — `load_store(data_dir)`: strict, fails the boot (`DataError`).
  Standard library only (`scripts/sync_data.py` reuses it).
- `api/dso/lookup.py` — the port. `api/dso/schemas.py` — HTTP shapes (crm-backend's
  `dso.types.ts`) and the mapping from the reference result shape.
- `api/health/routes.py` — liveness, readiness, `/healthz` alias.
- `scripts/sync_data.py` — the only writer of `data/` and `ATTRIBUTION.md`.

## Verify

```bash
ENV=test .venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy .
docker build -f Dockerfile.production -t dso-lookup:local .
```

Smoke test without the dev stack: `docker run --rm -d -p 127.0.0.1:18097:8000 -e
ENV=local --name dso-lookup-smoke dso-lookup:local`, then curl `/healthz`,
`/dso/meta`, `/dso/resolve?postcode=5020`; `docker logs` must hold no parameter;
`docker rm -f dso-lookup-smoke`. Never start or stop the monorepo's dev stack for it.

## Handoff order (this repository has no commits yet)

`dso-lookup/` is hidden from the monorepo by `.git/info/exclude`, not `.gitignore`.

1. Commit in order: (a) scaffold, code and tests; (b) data; (c) `build.yml`,
   `dso-data-bump.yml`, then `notify_monorepo_update.yml` LAST.
2. Create the PUBLIC repository `OptimCE/dso-lookup` (a private submodule breaks the
   monorepo's recursive checkout for every service) and push (a), (b), the build and
   bump workflows.
3. Make the GHCR package public; test an anonymous `docker pull
   ghcr.io/optimce/dso-lookup:main-prod`.
4. Secrets: `DSO_DATA_BOT_TOKEN` (PAT on OptimCE/dso-lookup only: Contents + Pull
   requests read/write; nothing on belgian-dso-map, which is public) and `MONOREPO_TOKEN`.
5. In the monorepo: remove the `dso-lookup/` line from `.git/info/exclude`, then
   `git submodule add ../dso-lookup.git dso-lookup` (never `-f`).
6. Push `notify_monorepo_update.yml` last, after the gitlink exists.
