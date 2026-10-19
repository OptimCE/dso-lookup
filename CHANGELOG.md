# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The data has its own versions (CalVer `YYYY.MM.N`, belgian-dso-map's `CHANGES.md`).
A data bump is listed here only by the version it pins.

## [Unreleased]

### Added

- The dso-lookup service: which distribution system operator runs the electricity
  and gas networks of a Belgian postcode or address, for crm-backend's meter-form
  GRD prefill. Internal (crm-backend is its only caller), not an annex.
- `GET /dso/resolve`, `GET /dso/resolve-address`, `GET /dso/operators`,
  `GET /dso/ean/{ean}/check` and `GET /dso/meta`, wrapped as
  `{"data", "error_code"}`; `GET /health/liveness`, `GET /health/readiness` and its
  alias `GET /healthz`.
- A line-for-line port of belgian-dso-map's reference `lookup.py`, checked against
  every row of the release's `vectors.csv` through HTTP.
- A strict loader of the pinned release in `data/`: a release that breaks the
  contract fails the boot.
- `scripts/sync_data.py`, the only writer of `data/`: checks the SHA256SUMS rule and
  the whole contract (content dates included), refuses drafts unless asked, never
  downgrades unless the release is named, and replaces `data/` all or nothing. Its
  flags and exit codes are the website's `sync_dso.py`'s, plus `--check`.
- The data bump workflow opens the "dso data X → Y" pull request when a newer
  release exists: started by belgian-dso-map's release (`repository_dispatch`), by
  a weekly cron and by hand.
- The EAN, carried in the path of `/dso/ean/{ean}/check`, never reaches a log:
  the shared error paths' records are rewritten to the route template.
- Data: belgian-dso-map `2026.10.0` (2026-10-19), its first published release
  (565 communes, 1,153 rows, 91 sources), under CC BY 4.0.
