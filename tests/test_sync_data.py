"""scripts/sync_data.py: checked, all or nothing, and never a draft by accident.

Every case syncs into a private `--data-dir` (never the repository's data/).
"""

import email.message
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.conftest import DATA_DIR, REPO_ROOT, write_sums

SCRIPT = REPO_ROOT / "scripts" / "sync_data.py"
TOKEN = "ghp_test-token-that-must-never-be-printed"
ASSETS = "https://api.github.com/repos/OptimCE/belgian-dso-map/releases/assets"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sync_data", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync_data = _load()


def _snapshot(directory: Path) -> dict[str, bytes]:
    if not directory.exists():
        return {}
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def _leftovers(parent: Path) -> list[str]:
    return sorted(
        p.name for p in parent.iterdir() if p.name.startswith((".data-new-", ".data-old-"))
    )


def _other(version: str) -> str:
    """Another valid version of the same kind (release or draft) as `version`."""
    year, month, n = sync_data.contract.version_key(version)
    return f"{year}.{month:02d}.{n + 9}"


def _retarget_version(directory: Path, old: str, new: str) -> None:
    """Rename the version in every file of a release copy, then re-sum it."""
    for path in directory.iterdir():
        if path.name != "SHA256SUMS":
            path.write_bytes(path.read_bytes().replace(old.encode(), new.encode()))
    write_sums(directory)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A built version: a copy of the shipped release (its SHA256SUMS untouched)."""
    directory = tmp_path / "dist"
    shutil.copytree(DATA_DIR, directory)
    return directory


@pytest.fixture
def version() -> str:
    value: str = json.loads((DATA_DIR / "release.json").read_bytes())["version"]
    return value


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A data/ that already holds the shipped release."""
    directory = tmp_path / "repo" / "data"
    shutil.copytree(DATA_DIR, directory)
    return directory


def _run(*args: str | Path) -> int:
    code: int = sync_data.main([str(arg) for arg in args])
    return code


# --- From a directory ------------------------------------------------------------------------


def test_sync_from_a_directory(source: Path, tmp_path: Path, version: str, capsys):
    """As belgian-dso-map ships it, with a REPORT.md: checked, then not kept."""
    (source / "REPORT.md").write_bytes(b"# Report\n\nFixture report.\n")
    write_sums(source)
    data = tmp_path / "fresh" / "data"
    flags = ["--allow-draft"] if version.endswith("-draft") else []
    assert _run("--from-dir", source, "--data-dir", data, *flags) == 0
    copied = _snapshot(data)
    expected = {k: v for k, v in _snapshot(source).items() if k != "REPORT.md"}
    assert copied == expected, "the files are the release's, byte for byte, minus REPORT.md"
    assert f"→ {version}" in capsys.readouterr().out
    assert _leftovers(data.parent) == []
    # The repository's ATTRIBUTION.md is derived from the release's, next to data/.
    attribution = (data.parent / "ATTRIBUTION.md").read_bytes()
    assert attribution == sync_data.render_attribution(copied["ATTRIBUTION.md"])
    assert attribution.startswith(
        b"# Attribution\n\n**The compiled data in `data/` are licensed by OptimCE under the "
        b"Creative Commons\nAttribution 4.0 International licence (CC BY 4.0),\n"
        b"<https://creativecommons.org/licenses/by/4.0/>.**"
    )
    assert b"Apache License 2.0 (`LICENSE`), which does not cover\n`data/`" in attribution
    assert b"not licensed" not in attribution


def test_a_second_run_changes_nothing(source: Path, target: Path, version: str, capsys):
    before = {p.name: p.stat().st_mtime_ns for p in target.iterdir()}
    flags = ["--allow-draft"] if version.endswith("-draft") else []
    assert _run("--version", version, "--from-dir", source, "--data-dir", target, *flags) == 0
    assert "already up to date" in capsys.readouterr().out
    assert {p.name: p.stat().st_mtime_ns for p in target.iterdir()} == before


def test_a_checksum_mismatch_leaves_data_untouched(source: Path, target: Path, capsys):
    before = _snapshot(target)
    operators = source / "operators.csv"
    operators.write_bytes(operators.read_bytes().replace(b"ORES", b"0RES", 1))  # not re-summed
    assert _run("--from-dir", source, "--data-dir", target, "--allow-draft") == 1
    assert "operators.csv does not match" in capsys.readouterr().err
    assert _snapshot(target) == before
    assert _leftovers(target.parent) == []


def test_a_contract_breach_leaves_data_untouched(source: Path, target: Path, capsys):
    before = _snapshot(target)
    path = source / "dso_lookup.csv"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    write_sums(source)
    assert _run("--from-dir", source, "--data-dir", target, "--allow-draft") == 1
    assert "BOM" in capsys.readouterr().err
    assert _snapshot(target) == before


def test_another_version_than_asked_is_refused(source: Path, target: Path, capsys):
    before = _snapshot(target)
    code = _run("--version", "2031.01.0", "--from-dir", source, "--data-dir", target)
    assert code == 1
    assert "asked for 2031.01.0" in capsys.readouterr().err
    assert _snapshot(target) == before


def test_a_draft_needs_allow_draft(source: Path, tmp_path: Path, version: str, capsys):
    if not version.endswith("-draft"):
        _retarget_version(source, version, version + "-draft")
    data = tmp_path / "fresh" / "data"
    assert _run("--from-dir", source, "--data-dir", data) == 1
    assert "is a draft" in capsys.readouterr().err
    assert not data.exists()


def test_allow_draft_needs_from_dir(tmp_path: Path, capsys):
    assert _run("--latest", "--allow-draft", "--data-dir", tmp_path / "data") == 2
    assert "--allow-draft needs --from-dir" in capsys.readouterr().err


def test_a_malformed_version_is_a_usage_error(tmp_path: Path):
    assert _run("--version", "2026.13.0", "--data-dir", tmp_path / "data") == 2
    assert _run("--version", "v2026.10.0", "--data-dir", tmp_path / "data") == 2


def test_check_reports_a_newer_release(source: Path, target: Path, tmp_path: Path, version: str):
    flags: list[str | Path] = ["--from-dir", source]
    assert _run("--check", *flags, "--data-dir", target) == 0
    assert _run("--check", *flags, "--data-dir", tmp_path / "empty") == 10
    release = json.loads((target / "release.json").read_bytes())
    release["version"] = "2020.01.0"
    (target / "release.json").write_text(json.dumps(release), encoding="utf-8")
    before = _snapshot(target)
    assert _run("--check", *flags, "--data-dir", target) == 10
    assert _snapshot(target) == before, "--check writes nothing"


def test_a_release_outranks_its_own_draft():
    order = sync_data.contract.release_order
    assert order("2026.10.0") > order("2026.10.0-draft")
    assert order("2026.10.0-draft") > order("2026.09.3")
    assert order("2026.11.0-draft") > order("2026.10.0")


def test_a_failed_swap_restores_data(source: Path, target: Path, version: str, monkeypatch, capsys):
    """The rename that puts the new directory in place fails: the old one comes back."""
    before = _snapshot(target)
    base = version.removesuffix("-draft")
    _retarget_version(source, base, _other(base))  # a different, valid release
    real_replace = os.replace
    calls: list[tuple[str, str]] = []

    def flaky_replace(src, dst):
        calls.append((Path(src).name, Path(dst).name))
        if Path(src).name.startswith(".data-new-"):
            raise PermissionError("simulated: data/ is in use")
        return real_replace(src, dst)

    monkeypatch.setattr(sync_data.os, "replace", flaky_replace)
    assert _run("--from-dir", source, "--data-dir", target, "--allow-draft") == 1
    assert "simulated" in capsys.readouterr().err
    assert len(calls) == 3, calls  # data -> backup, staging -> data (fails), backup -> data
    assert _snapshot(target) == before
    assert _leftovers(target.parent) == []


def test_pr_body_and_github_output(
    source: Path, target: Path, tmp_path: Path, version: str, monkeypatch
):
    base = version.removesuffix("-draft")
    _retarget_version(source, base, _other(base))
    changes = (source / "CHANGES.md").read_text(encoding="utf-8")
    assert f"## {_other(base)} — " in changes
    output = tmp_path / "github_output"
    body = tmp_path / "body.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    code = _run(
        "--from-dir",
        source,
        "--data-dir",
        target,
        "--allow-draft",
        "--pr-body",
        body,
        "--github-output",
    )
    assert code == 0
    old = json.loads((DATA_DIR / "release.json").read_bytes())["version"]
    new = json.loads((target / "release.json").read_bytes())["version"]
    assert output.read_text(encoding="utf-8") == f"changed=true\nfrom={old}\nto={new}\n"
    text = body.read_text(encoding="utf-8")
    assert f"dso data {old} → {new}" in text
    assert f"## {_other(base)} — " in text
    assert "\r" not in text


def test_the_script_runs_standalone(source: Path, tmp_path: Path, version: str):
    """As CI and the bump workflow run it: a plain interpreter, from any directory."""
    data = tmp_path / "standalone" / "data"
    flags = ["--allow-draft"] if version.endswith("-draft") else []
    result = subprocess.run(  # noqa: S603  # our own script, fixed arguments
        [
            sys.executable,
            str(SCRIPT),
            "--from-dir",
            str(source),
            "--data-dir",
            str(data),
            *flags,
        ],
        capture_output=True,
        cwd=tmp_path,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert (data / "dso_lookup.csv").read_bytes() == (DATA_DIR / "dso_lookup.csv").read_bytes()


# --- From GitHub --------------------------------------------------------------------------------


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@pytest.fixture
def github(source: Path, version: str, monkeypatch) -> Callable[..., list[Any]]:
    """A fake GitHub API serving `source` as a published (non-draft) release."""
    release = version.removesuffix("-draft")
    if release != version:
        _retarget_version(source, version, release)
    files = _snapshot(source)
    requests: list[Any] = []

    def install(
        *,
        status: int | None = None,
        tag: str = release,
        token: str | None = TOKEN,
        **flags: bool,
    ) -> list[Any]:
        """`token=None`: DSO_MAP_TOKEN unset; any string (even empty): set to it."""
        assets = [{"name": name, "url": f"{ASSETS}/{i}"} for i, name in enumerate(sorted(files))]
        by_url = {asset["url"]: files[asset["name"]] for asset in assets}
        metadata = json.dumps({"tag_name": tag, "assets": assets, **flags}).encode()

        def urlopen(request, timeout=None):
            requests.append(request)
            if status is not None:
                raise sync_data.urllib.error.HTTPError(
                    request.full_url, status, "nope", email.message.Message(), None
                )
            if request.full_url in by_url:
                return _FakeResponse(by_url[request.full_url])
            return _FakeResponse(metadata)

        monkeypatch.setattr(sync_data._OPENER, "open", urlopen)
        if token is None:
            monkeypatch.delenv("DSO_MAP_TOKEN", raising=False)
        else:
            monkeypatch.setenv("DSO_MAP_TOKEN", token)
        return requests

    return install


def test_sync_from_github(github, tmp_path: Path, capfd):
    requests = github()
    data = tmp_path / "gh" / "data"
    assert _run("--latest", "--data-dir", data) == 0
    assert (
        requests[0].full_url
        == "https://api.github.com/repos/OptimCE/belgian-dso-map/releases/latest"
    )
    for request in requests:
        # Unredirected: the 302 to the signed storage URL never carries the token.
        assert request.unredirected_hdrs.get("Authorization") == f"Bearer {TOKEN}"
        assert "Authorization" not in request.headers
    assets = [r for r in requests if "/releases/assets/" in r.full_url]
    assert assets and all(r.headers["Accept"] == "application/octet-stream" for r in assets)
    assert "REPORT.md" not in _snapshot(data)
    captured = capfd.readouterr()
    assert TOKEN not in captured.out + captured.err


def test_a_github_error_is_reported_without_the_token(github, tmp_path: Path, capfd):
    github(status=404)
    assert _run("--version", "2026.10.0", "--data-dir", tmp_path / "data") == 1
    captured = capfd.readouterr()
    assert "HTTP 404" in captured.err
    assert "DSO_MAP_TOKEN cannot read" in captured.err
    assert TOKEN not in captured.out + captured.err


@pytest.mark.parametrize("token", [None, "", "  \n"], ids=["unset", "empty", "blank"])
def test_sync_from_github_anonymously(github, tmp_path: Path, version: str, token: str | None):
    """belgian-dso-map is public: without DSO_MAP_TOKEN the releases are read
    anonymously, and no request carries an Authorization header at all."""
    requests = github(token=token)
    data = tmp_path / "anon" / "data"
    assert _run("--latest", "--data-dir", data) == 0
    assert len(requests) > 1, "the metadata, then the assets"
    for request in requests:
        assert not request.has_header("Authorization"), request.full_url
        assert "Authorization" not in request.unredirected_hdrs
        assert "Authorization" not in request.headers
    assets = [r for r in requests if "/releases/assets/" in r.full_url]
    assert assets and all(r.headers["Accept"] == "application/octet-stream" for r in assets)
    expected = version.removesuffix("-draft")
    assert json.loads((data / "release.json").read_bytes())["version"] == expected
    assert (data.parent / "ATTRIBUTION.md").is_file()


def test_an_anonymous_404_does_not_blame_a_token(github, tmp_path: Path, capsys):
    github(status=404, token=None)
    assert _run("--version", "2026.10.0", "--data-dir", tmp_path / "data") == 1
    err = capsys.readouterr().err
    assert "HTTP 404 (no such release)" in err
    assert "cannot read" not in err


@pytest.mark.parametrize("status", [403, 429])
def test_an_anonymous_rate_limit_suggests_a_token(github, tmp_path: Path, capsys, status: int):
    github(status=status, token="")
    assert _run("--latest", "--data-dir", tmp_path / "data") == 1
    err = capsys.readouterr().err
    assert f"HTTP {status} (anonymous requests are rate-limited: set DSO_MAP_TOKEN" in err


def test_a_published_draft_is_refused(github, tmp_path: Path, capsys):
    github(tag="2026.10.0-draft")
    assert _run("--latest", "--data-dir", tmp_path / "data") == 1
    assert "is a draft" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/x",
        "https://api.github.com/repos/someone/else/releases/assets/1",  # another repository
        "https://api.github.com/repos/OptimCE/belgian-dso-map/zipball/2026.10.0",  # not an asset
    ],
)
def test_only_the_assets_of_the_release_are_fetched(
    github, tmp_path: Path, monkeypatch, capsys, url: str
):
    """The website's rule: an asset URL is `/repos/{repo}/releases/assets/…` of the API."""
    requests = github()
    real = sync_data.download_assets

    def poisoned(repo, metadata):
        metadata = dict(metadata)
        metadata["assets"] = [{"name": "dso_lookup.csv", "url": url}]
        return real(repo, metadata)

    monkeypatch.setattr(sync_data, "download_assets", poisoned)
    assert _run("--latest", "--data-dir", tmp_path / "data") == 1
    assert "is not an asset of OptimCE/belgian-dso-map" in capsys.readouterr().err
    assert len(requests) == 1, "only the release metadata was fetched"


def test_nothing_but_the_github_api_is_ever_fetched():
    with pytest.raises(sync_data.SyncError, match="not the GitHub API"):
        sync_data._get("https://evil.example/x", "application/octet-stream")


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_a_release_github_marks_draft_or_prerelease_is_refused(
    github, tmp_path: Path, capsys, flag: str
):
    """Its tag may look published (`2026.10.0`): the website refuses it, so does this."""
    github(**{flag: True})
    data = tmp_path / "data"
    assert _run("--latest", "--data-dir", data) == 1
    assert "is a draft or a prerelease" in capsys.readouterr().err
    assert not data.exists()


def test_redirects_are_followed_over_https_only():
    handler = sync_data._HttpsOnlyRedirect()
    request = sync_data.urllib.request.Request(f"{ASSETS}/1")
    request.add_unredirected_header("Authorization", f"Bearer {TOKEN}")
    headers = email.message.Message()
    with pytest.raises(sync_data.SyncError, match="non-https"):
        handler.redirect_request(request, None, 302, "Found", headers, "http://storage.example/x")
    followed = handler.redirect_request(
        request, None, 302, "Found", headers, "https://storage.example/x"
    )
    assert followed is not None
    assert followed.full_url == "https://storage.example/x"
    assert not followed.has_header("Authorization"), "the token never follows a redirect"
    assert sync_data._HttpsOnlyRedirect in {type(h) for h in sync_data._OPENER.handlers}


# --- Never a silent downgrade; --if-newer, --dry-run; the website's flags -------------------


def _older_source(source: Path, version: str) -> str:
    """Make `source` a valid release OLDER than the shipped one; return its version."""
    base = version.removesuffix("-draft")
    year, month, _ = sync_data.contract.version_key(base)
    older_base = f"{year - 1}.{month:02d}.0"
    _retarget_version(source, base, older_base)
    return older_base + ("-draft" if version.endswith("-draft") else "")


def test_an_older_release_is_refused_unless_named(source: Path, target: Path, version: str, capsys):
    """GitHub's "latest" can point at an older tag (a re-published old release becomes
    latest by default): the bump must never open that downgrade as a pull request."""
    older = _older_source(source, version)
    before = _snapshot(target)
    assert _run("--from-dir", source, "--data-dir", target, "--allow-draft") == 1
    err = capsys.readouterr().err
    assert f"{older} is older than data/'s {version}" in err
    assert f"--version {older}" in err
    assert _snapshot(target) == before
    # Named, it is a deliberate rollback.
    code = _run("--version", older, "--from-dir", source, "--data-dir", target, "--allow-draft")
    assert code == 0
    assert "rolling data/ back" in capsys.readouterr().err
    assert json.loads((target / "release.json").read_bytes())["version"] == older


def test_latest_on_github_never_downgrades(github, target: Path, capsys):
    github(tag="2020.01.0")
    before = _snapshot(target)
    assert _run("--latest", "--data-dir", target) == 1
    assert "is older than data/'s" in capsys.readouterr().err
    assert _snapshot(target) == before


def test_if_newer_reports_an_older_or_equal_release_as_up_to_date(
    source: Path, target: Path, tmp_path: Path, version: str, monkeypatch, capsys
):
    output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    before = {p.name: p.stat().st_mtime_ns for p in target.iterdir()}
    flags: list[str | Path] = ["--allow-draft", "--if-newer", "--github-output"]
    flags += ["--data-dir", target]
    assert _run("--from-dir", source, *flags) == 0  # the same release
    _older_source(source, version)
    assert _run("--from-dir", source, *flags) == 0  # an older one
    assert capsys.readouterr().out.count("up to date: data/ holds") == 2
    expected = f"changed=false\nfrom={version}\nto={version}\n"
    assert output.read_text(encoding="utf-8") == expected * 2
    assert {p.name: p.stat().st_mtime_ns for p in target.iterdir()} == before


def test_if_newer_syncs_a_newer_release(source: Path, target: Path, version: str):
    base = version.removesuffix("-draft")
    _retarget_version(source, base, _other(base))
    assert _run("--from-dir", source, "--data-dir", target, "--allow-draft", "--if-newer") == 0
    new = json.loads((target / "release.json").read_bytes())["version"]
    assert sync_data.contract.release_order(new) > sync_data.contract.release_order(version)


def test_dry_run_writes_neither_data_nor_attribution(
    source: Path, tmp_path: Path, version: str, capsys
):
    data = tmp_path / "dry" / "data"
    flags = ["--allow-draft"] if version.endswith("-draft") else []
    assert _run("--from-dir", source, "--data-dir", data, "--dry-run", *flags) == 0
    out = capsys.readouterr().out
    assert f"would sync (none) → {version}" in out
    assert "ATTRIBUTION.md would be rewritten" in out
    assert not data.exists()
    assert not (data.parent / "ATTRIBUTION.md").exists()


def test_from_dir_alone_reads_its_version(source: Path, tmp_path: Path, version: str):
    """As the website's sync_dso.py: `--from-dir X` needs neither --version nor --latest."""
    data = tmp_path / "alone" / "data"
    flags = ["--allow-draft"] if version.endswith("-draft") else []
    assert _run("--from-dir", source, "--data-dir", data, *flags) == 0
    assert json.loads((data / "release.json").read_bytes())["version"] == version


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--latest", "--from-dir", "x"], "--latest reads GitHub"),
        ([], "give --version X, --latest or --from-dir PATH"),
        (["--version", "2026.10.0-draft"], "only read with --from-dir"),
        (["--latest", "--repo", "not a repo"], "is not OWNER/NAME"),
    ],
)
def test_usage_errors_as_the_website(tmp_path: Path, capsys, args: list[str], message: str):
    assert _run(*args, "--data-dir", tmp_path / "data") == 2
    assert message in capsys.readouterr().err


def test_the_token_is_only_dso_map_token():
    """Optional, and never an ambient one: a developer's GITHUB_TOKEN (or GH_TOKEN)
    is not picked up; the bump workflow says what goes into DSO_MAP_TOKEN."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"DSO_MAP_TOKEN"' in text
    for ambient in ("GITHUB_TOKEN", "GH_TOKEN"):
        assert ambient not in text, f"{ambient}: the script reads DSO_MAP_TOKEN only"
    assert "add_unredirected_header" in text
