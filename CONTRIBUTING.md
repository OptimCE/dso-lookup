# Contributing to OptimCE dso-lookup

Thank you for your interest in contributing! Issues and pull requests are
welcome from everyone. By participating in this project, you agree to abide by
our [Code of Conduct](CODE_OF_CONDUCT.md).

This repository holds the **dso-lookup service** — the internal lookup of the
distribution system operator (DSO) of a Belgian address — one of several
repositories under the [OptimCE organization](https://github.com/OptimCE). See the
[README](README.md) for what the service does and how it fits into the platform.

**The data is not maintained here.** `data/` is a pinned release of
belgian-dso-map, written only by `scripts/sync_data.py`. A wrong operator for a
commune is corrected in belgian-dso-map, then reaches this repository through the
weekly "dso data X → Y" pull request. A pull request that edits `data/` by hand
fails the tests (`tests/test_data_integrity.py`).

## Setting Up a Development Environment

The full OptimCE platform (gateway, authentication, databases, and the other
services) runs from the [monorepo](https://github.com/OptimCE/monorepo) via
Docker Compose. To work on dso-lookup on its own:

```bash
git clone https://github.com/OptimCE/dso-lookup.git
cd dso-lookup
python -m venv .venv
# install the development and test dependencies
.venv/Scripts/python.exe -m pip install -r requirements/all.txt
```

The service requires Python 3.12. Copy `.env.exemple` to `.env.local` if you need
to change a setting, then run it:

```bash
uvicorn main:app --reload --no-access-log
```

## Before You Open a Pull Request

Run the same checks CI runs
([`.github/workflows/`](.github/workflows) — `lint.yml`, `test.yml`, `build.yml`):

```bash
ENV=test .venv/Scripts/python.exe -m pytest -q          # full test suite (no Docker needed)
.venv/Scripts/python.exe -m ruff check .                # lint
.venv/Scripts/python.exe -m ruff format --check .       # formatting
.venv/Scripts/python.exe -m mypy .                      # types
```

## Reporting Bugs and Suggesting Features

Open a [GitHub issue](https://github.com/OptimCE/dso-lookup/issues). For bugs,
include what you did, what you expected, and what happened instead — logs and
reproduction steps help a lot.

For security vulnerabilities, **do not open a public issue**; follow the
[security policy](SECURITY.md) instead.

## Submitting Pull Requests

1. Fork the repository and create a feature branch from `main`.
2. Make your changes. Keep each pull request focused on a single topic.
3. Make sure the checks above pass locally.
4. Open a pull request against `main`, describing **what** you changed and
   **why**.

Small documentation fixes are welcome as direct pull requests; for larger
changes, opening an issue first to discuss the approach can save you time.

## Commit Messages

Use short, imperative commit messages, preferably following the
[Conventional Commits](https://www.conventionalcommits.org/) style used in this
repository:

```
feat: answer choose_section with every candidate's operator
fix: keep the store strict on a padded cell
chore(data): dso data 2026.10.0 → 2026.11.0
docs: document the readiness rules
```

## License

The code of OptimCE dso-lookup is licensed under the [Apache License 2.0](LICENSE)
(the data in `data/`, a belgian-dso-map release, are licensed by OptimCE under CC BY 4.0:
see [ATTRIBUTION.md](ATTRIBUTION.md)). By
contributing, you agree that your contributions will be licensed under the same
license.
