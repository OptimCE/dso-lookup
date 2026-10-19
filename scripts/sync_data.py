"""Pin a belgian-dso-map release into data/ (the ONLY way data/ changes).

    python scripts/sync_data.py --latest --if-newer          # the weekly bump
    python scripts/sync_data.py --version 2026.10.0
    python scripts/sync_data.py --latest --check
    python scripts/sync_data.py --from-dir ../../belgian-dso-map/dist
    python scripts/sync_data.py --from-dir ../../belgian-dso-map/.build/mini --allow-draft

The flags and exit codes of the website's `scripts/sync_dso.py` (one behaviour
in both consumers), plus two of this repository's own, `--check` and
`--data-dir`:

- `--version X` or `--latest` picks a GitHub Release of `--repo`, through the
  REST API (no `gh`): `releases/tags/X` or `releases/latest`, then every asset
  from its `url` (`/repos/{repo}/releases/assets/…` only) with
  `Accept: application/octet-stream`. A release GitHub marks `draft` or
  `prerelease` is refused. belgian-dso-map is public: without a token the
  releases are read anonymously (no Authorization header at all). A token is
  optional and is `DSO_MAP_TOKEN` only, never an ambient one: when set and not
  empty, any GitHub token (it only lifts the anonymous API rate limit, 60
  requests an hour per IP; the bump workflow passes its own `github.token`). It
  is sent with `add_unredirected_header`, so the redirect to the signed storage
  URL never carries it; redirects are followed over https only. The token is
  never printed.
- `--from-dir DIR` reads a built version instead (`belgian-dso-map/dist`, or
  `.build/mini` / `.build/draft` before the first release); `--version`, if
  given, must equal its release.json. `--latest` reads GitHub: it is refused
  with `--from-dir`.
- A draft (`-draft`) is refused unless `--from-dir … --allow-draft`. CI refuses
  draft data anyway (tests/test_ci_guard.py).
- `--if-newer`: nothing happens unless the release is newer than data/'s
  (a release outranks its own draft). The bump workflow passes it.
- Never a silent downgrade: a release OLDER than data/'s is refused unless it is
  named with `--version` (a deliberate rollback). sync_dso.py prints a note and
  proceeds; this script refuses, because its bump would open the downgrade as a
  pull request to merge.
- `--dry-run` checks and reports, and writes neither data/ nor ATTRIBUTION.md
  (`--pr-body` and `--github-output` are still written).
- `--check` writes nothing: exit 10 when the chosen release is newer than data/,
  0 when data/ is up to date.

Before anything is written the release is checked exactly as the service checks
it at boot (`api.dso.store.load_files`): the SHA256SUMS rule (every file present
is listed and matches, the required set is present, REPORT.md optional), the
contract dialect and headers, one data_version equal to release.json, the
content dates, the CHANGES.md grammar and the counts. Then data/ is replaced ALL
OR NOTHING: the files are written to a sibling directory, read back and checked
again, and swapped in by renames. On any failure data/ is left exactly as it
was. REPORT.md is not kept.

Exit codes: 0 synced or up to date, 1 refused or failed (integrity, contract,
draft, version, downgrade, download, I/O), 2 usage, 10 newer release (--check).
`--pr-body FILE` and `--github-output` serve .github/workflows/dso-data-bump.yml.

Standard library only.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.dso import contract  # noqa: E402
from api.dso.contract import DataError  # noqa: E402
from api.dso.store import DsoStore, load_files, load_store, read_release_files  # noqa: E402

DEFAULT_REPO = "OptimCE/belgian-dso-map"
DEFAULT_DATA_DIR = ROOT / "data"
API = "https://api.github.com"
TOKEN_ENV = "DSO_MAP_TOKEN"  # noqa: S105  # the NAME of the variable, not a secret
MAX_ASSET_BYTES = 64 * 1024 * 1024
REQUESTED_VERSION_RE = re.compile(r"[0-9]{4}\.(0[1-9]|1[0-2])\.(0|[1-9][0-9]*)(-draft)?")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

EXIT_OK = 0
EXIT_REFUSED = 1  # refused or failed: integrity, contract, draft, downgrade, download, I/O
EXIT_USAGE = 2
EXIT_NEWER = 10


class SyncError(Exception):
    """A failure with an exit code. The message never holds the token."""

    def __init__(self, message: str, code: int = EXIT_REFUSED) -> None:
        super().__init__(message)
        self.code = code


# --- GitHub ----------------------------------------------------------------------------------


class _HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects (an asset answers 302 to a storage host) over https only.

    urllib copies only the ordinary headers to the redirected request: the
    Authorization header, added with add_unredirected_header, is dropped.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if urlsplit(newurl).scheme != "https":
            raise SyncError(f"refused a redirect to a non-https URL (HTTP {code})")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HttpsOnlyRedirect())


def _http_error_hint(code: int, *, authenticated: bool) -> str:
    """What an HTTP error most likely means, with or without DSO_MAP_TOKEN."""
    if authenticated and code in (401, 403, 404):
        return f" (no such release, or {TOKEN_ENV} cannot read the repository)"
    if code == 404:
        return " (no such release)"
    if code in (403, 429):
        return f" (anonymous requests are rate-limited: set {TOKEN_ENV} to any GitHub token)"
    return ""


def _get(url: str, accept: str) -> bytes:
    if not url.startswith(f"{API}/"):
        raise SyncError(f"refusing to fetch {url!r}: not the GitHub API")
    request = urllib.request.Request(  # noqa: S310  # https://api.github.com only, checked above
        url,
        headers={
            "Accept": accept,
            "User-Agent": "optimce-dso-lookup-sync-data",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    # Optional: unset or empty (or blank) = anonymous, with no Authorization header.
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        # Unredirected: the asset URL answers 302 to a signed storage URL that
        # must not receive (and rejects) the token.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(request, timeout=60) as response:
            data: bytes = response.read(MAX_ASSET_BYTES + 1)
    except urllib.error.HTTPError as exc:
        hint = _http_error_hint(exc.code, authenticated=bool(token))
        raise SyncError(f"GET {url}: HTTP {exc.code}{hint}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise SyncError(f"GET {url}: {reason}") from None
    if len(data) > MAX_ASSET_BYTES:
        raise SyncError(f"GET {url}: larger than {MAX_ASSET_BYTES} bytes")
    return data


def release_metadata(repo: str, version: str | None) -> dict[str, Any]:
    path = f"releases/tags/{version}" if version else "releases/latest"
    data = _get(f"{API}/repos/{repo}/{path}", "application/vnd.github+json")
    try:
        metadata = json.loads(data)
    except json.JSONDecodeError:
        raise SyncError(f"{repo} {path}: the answer is not JSON") from None
    if not isinstance(metadata, dict) or not isinstance(metadata.get("tag_name"), str):
        raise SyncError(f"{repo} {path}: no tag_name in the answer")
    if metadata.get("draft") or metadata.get("prerelease"):
        raise SyncError(f"release {metadata['tag_name']} is a draft or a prerelease: refused")
    return metadata


def download_assets(repo: str, metadata: Mapping[str, Any]) -> dict[str, bytes]:
    assets = metadata.get("assets")
    if not isinstance(assets, list):
        raise SyncError("the release lists no assets")
    prefix = f"{API}/repos/{repo}/releases/assets/".lower()  # GitHub names are case-insensitive
    files: dict[str, bytes] = {}
    for asset in assets:
        name, url = asset.get("name"), asset.get("url")
        if name not in contract.RELEASE_FILES or name in files or not isinstance(url, str):
            raise SyncError(f"unexpected release asset {name!r}")
        if not url.lower().startswith(prefix):
            raise SyncError(f"asset {name}: {url!r} is not an asset of {repo}")
        files[name] = _get(url, "application/octet-stream")
    return files


# --- data/ -------------------------------------------------------------------------------------


def current_version(data_dir: Path) -> str | None:
    """The version pinned in data/, or None when there is none (or it is unreadable)."""
    try:
        return contract.parse_release((data_dir / "release.json").read_bytes()).version
    except (OSError, DataError):
        return None


def same_files(data_dir: Path, files: Mapping[str, bytes]) -> bool:
    try:
        present = read_release_files(data_dir)
    except (OSError, DataError):
        return False
    return present == dict(files)


def write_atomically(data_dir: Path, files: Mapping[str, bytes]) -> DsoStore:
    """Replace data_dir with exactly `files`, all or nothing. Returns the store read back."""
    parent = data_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".data-new-", dir=parent))
    backup: Path | None = None
    try:
        for name, content in files.items():
            (staging / name).write_bytes(content)
        store = load_store(staging)  # what lands is what was checked, read back from disk
        if data_dir.exists():
            backup = Path(tempfile.mkdtemp(prefix=".data-old-", dir=parent))
            backup.rmdir()
            os.replace(data_dir, backup)
        try:
            os.replace(staging, data_dir)
        except BaseException:
            if backup is not None:
                os.replace(backup, data_dir)
                backup = None
            raise
        return store
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)


# --- ATTRIBUTION.md -------------------------------------------------------------------------------

# The repository's ATTRIBUTION.md is DERIVED: this header, then the release's own
# ATTRIBUTION.md (also data/ATTRIBUTION.md) without its title. Rewritten with
# every sync, so the credits always match the data the image ships.
ATTRIBUTION_HEADER = """# Attribution

**The compiled data in `data/` are licensed by OptimCE under the Creative Commons
Attribution 4.0 International licence (CC BY 4.0),
<https://creativecommons.org/licenses/by/4.0/>.** They are a release of the public
dataset belgian-dso-map, <https://github.com/OptimCE/belgian-dso-map>, pinned byte for
byte; this repository and its public image `ghcr.io/optimce/dso-lookup` serve it
unchanged. The release's own text below says how to credit it. The code of this
repository is licensed under the Apache License 2.0 (`LICENSE`), which does not cover
`data/`. The sources of the data keep their own licences and attribution requirements:
they are credited below.

Everything after the line below is the release's own `ATTRIBUTION.md`, without
its title (`data/ATTRIBUTION.md` holds it byte for byte). `scripts/sync_data.py`
rewrites this file with every data release: never edit it by hand.

---
"""


def render_attribution(release_attribution: bytes) -> bytes:
    """The repository's ATTRIBUTION.md for a release's ATTRIBUTION.md."""
    lines = release_attribution.decode("utf-8").split("\n")
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    return (ATTRIBUTION_HEADER + "\n" + "\n".join(lines)).encode("utf-8")


def sync_attribution(path: Path, release_attribution: bytes, *, dry_run: bool = False) -> bool:
    """Bring `path` in line with the release; True when it had (or, dry, would have) to change."""
    wanted = render_attribution(release_attribution)
    try:
        if path.read_bytes() == wanted:
            return False
    except FileNotFoundError:
        pass
    if dry_run:
        return True
    staging = path.with_name(path.name + ".new")
    staging.write_bytes(wanted)
    os.replace(staging, path)
    return True


# --- Reports -----------------------------------------------------------------------------------


def _counts_line(counts: Mapping[str, Any]) -> str:
    by_region = counts["communes_by_region"]
    return (
        f"{counts['communes']} communes (BRU {by_region['BRU']}, VLA {by_region['VLA']}, "
        f"WAL {by_region['WAL']}), {counts['postcodes']} postcodes, {counts['rows']} rows, "
        f"{counts['operators']} operators, {counts['notes']} notes, {counts['sources']} sources"
    )


def changes_since(changes_md: bytes, old: str | None) -> list[contract.ChangesSection]:
    """The CHANGES.md sections newer than `old` (all of them without an old version)."""
    sections = contract.parse_changes(changes_md.decode("utf-8"))
    if old is None:
        return sections[:1]
    floor = contract.release_order(old)
    return [s for s in sections if contract.release_order(s.version) > floor] or sections[:1]


def pr_body(old: str | None, store: DsoStore, files: Mapping[str, bytes]) -> str:
    new = store.release.version
    lines = [
        f"## dso data {old or '(none)'} → {new}",
        "",
        f"Pins the belgian-dso-map release **{new}** ({store.release.date}) into `data/`, "
        "with `scripts/sync_data.py`. The files are the release's, byte for byte "
        "(SHA256SUMS checked; REPORT.md not kept).",
        "",
        f"- {_counts_line(store.counts)}",
        "",
        "Merging publishes `:main-prod` (`build.yml` watches `data/**`). The vectors of "
        "the new release run in `test.yml` on this pull request.",
        "",
        "### CHANGES",
        "",
        "```text",
    ]
    for section in changes_since(files["CHANGES.md"], old):
        lines.append(contract.render_section(section).rstrip("\n"))
        lines.append("")
    lines[-1] = "```"
    return "\n".join(lines) + "\n"


def write_github_output(values: Mapping[str, str]) -> None:
    target = os.environ.get("GITHUB_OUTPUT", "")
    if not target:
        raise SyncError("--github-output needs the GITHUB_OUTPUT environment variable", EXIT_USAGE)
    with open(target, "a", encoding="utf-8", newline="\n") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


# --- Main ---------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pin a belgian-dso-map release into data/ (checked, all or nothing).",
        epilog="Exit codes: 0 synced or up to date, 1 refused or failed, 2 usage error, "
        "10 newer release (--check).",
    )
    which = parser.add_mutually_exclusive_group()
    which.add_argument(
        "--version", help="YYYY.MM.N (YYYY.MM.N-draft with --from-dir only); may roll back"
    )
    which.add_argument("--latest", action="store_true", help="the latest GitHub release")
    parser.add_argument("--from-dir", type=Path, help="a built version instead of GitHub")
    parser.add_argument(
        "--allow-draft", action="store_true", help="accept a -draft build (needs --from-dir)"
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"default: {DEFAULT_REPO}")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="default: data/")
    parser.add_argument(
        "--if-newer", action="store_true", help="do nothing unless the release is newer"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check and report, write neither data/ nor ATTRIBUTION.md",
    )
    parser.add_argument(
        "--check", action="store_true", help="write nothing; exit 10 if a newer release exists"
    )
    parser.add_argument("--pr-body", type=Path, help="write a pull request body to this file")
    parser.add_argument(
        "--github-output", action="store_true", help="append changed/from/to to $GITHUB_OUTPUT"
    )
    return parser


def _check_usage(args: argparse.Namespace) -> None:
    if args.from_dir is None and not (args.version or args.latest):
        raise SyncError("give --version X, --latest or --from-dir PATH", EXIT_USAGE)
    if args.from_dir is not None and args.latest:
        raise SyncError("--latest reads GitHub; it cannot be combined with --from-dir", EXIT_USAGE)
    if args.allow_draft and args.from_dir is None:
        raise SyncError("--allow-draft needs --from-dir: a draft is never downloaded", EXIT_USAGE)
    if args.version is not None:
        if not REQUESTED_VERSION_RE.fullmatch(args.version):
            raise SyncError(f"--version {args.version!r} is not YYYY.MM.N", EXIT_USAGE)
        if contract.is_draft(args.version) and args.from_dir is None:
            raise SyncError(
                "a -draft version is only read with --from-dir … --allow-draft", EXIT_USAGE
            )
    if not REPO_RE.fullmatch(args.repo):
        raise SyncError(f"--repo {args.repo!r} is not OWNER/NAME", EXIT_USAGE)


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _check_usage(args)

    data_dir: Path = args.data_dir
    old = current_version(data_dir)

    metadata: dict[str, Any] | None = None
    files: dict[str, bytes] | None = None
    if args.from_dir is not None:
        try:
            files = read_release_files(args.from_dir)
        except OSError as exc:
            raise SyncError(f"{args.from_dir}: {exc}") from None
        if "release.json" not in files:
            raise SyncError(f"{args.from_dir}: no release.json")
        target = contract.parse_release(files["release.json"]).version
    else:
        metadata = release_metadata(args.repo, args.version)
        target = metadata["tag_name"]

    if args.version is not None and target != args.version:
        raise SyncError(f"asked for {args.version}, the source holds {target}")
    try:
        contract.version_key(target)
    except ValueError as exc:
        raise SyncError(f"release {target!r}: {exc}") from None
    newer = old is None or contract.release_order(target) > contract.release_order(old)

    if args.check:
        print(f"data/ holds {old or '(nothing)'}; {target} is {'newer' if newer else 'not newer'}")
        return EXIT_NEWER if newer else EXIT_OK

    if args.if_newer and not newer:
        print(f"up to date: data/ holds {old}, the release is {target}")
        if args.github_output:
            write_github_output({"changed": "false", "from": old or "", "to": old or ""})
        return EXIT_OK

    if old is not None and contract.release_order(target) < contract.release_order(old):
        if args.version is None:
            raise SyncError(
                f"{target} is older than data/'s {old}: refused. A rollback names the "
                f"release: --version {target}"
            )
        print(f"note: rolling data/ back from {old} to {target}", file=sys.stderr)

    if contract.is_draft(target) and not (args.from_dir is not None and args.allow_draft):
        raise SyncError(f"{target} is a draft: only `--from-dir … --allow-draft` takes one")

    if files is None:
        if metadata is None:  # unreachable: one of --from-dir / GitHub always ran
            raise SyncError("no release source", EXIT_USAGE)
        files = download_assets(args.repo, metadata)

    # The consumer-side SHA256SUMS rule on everything that came, REPORT.md included;
    # then REPORT.md is dropped (optional) and the rest checked as the service would.
    contract.verify_integrity(files)
    files.pop("REPORT.md", None)
    store = load_files(files)
    if store.release.version != target:
        raise SyncError(f"release.json says {store.release.version}, the release is {target}")

    changed = not same_files(data_dir, files)
    if changed and not args.dry_run:
        store = write_atomically(data_dir, files)
    if changed:
        verb = "would sync" if args.dry_run else "dso data"
        print(f"{verb} {old or '(none)'} → {target} ({store.release.date})")
    else:
        print(f"dso data {target} ({store.release.date}): data/ is already up to date")
    attribution = data_dir.parent / "ATTRIBUTION.md"
    if sync_attribution(attribution, files["ATTRIBUTION.md"], dry_run=args.dry_run):
        changed = True
        print("ATTRIBUTION.md " + ("would be " if args.dry_run else "") + "rewritten")
    if args.dry_run:
        print("dry run: nothing written to data/ or ATTRIBUTION.md")
    print(_counts_line(store.counts))
    for section in changes_since(files["CHANGES.md"], old if changed else None):
        print(contract.render_section(section), end="")

    if args.pr_body is not None:
        args.pr_body.write_bytes(pr_body(old, store, files).encode("utf-8"))
    if args.github_output:
        write_github_output(
            {"changed": "true" if changed else "false", "from": old or "", "to": target}
        )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    try:
        return run(argv)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except DataError as exc:
        print(f"error: refused, data/ untouched: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
