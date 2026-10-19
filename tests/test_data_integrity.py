"""data/ is a belgian-dso-map release, byte for byte.

Only scripts/sync_data.py writes data/. A hand edit, an editor that adds a BOM
or git's autocrlf turning LF into CRLF on Windows would all change the bytes; the
consumer-side SHA256SUMS rule catches every one of them:

- every file present (other than SHA256SUMS) is listed and matches;
- the required set is present;
- REPORT.md is optional (this repository does not keep it).

Never a plain `sha256sum -c` on this copy: it would fail on the absent REPORT.md.
"""

import hashlib
import json
import shutil
import subprocess

import pytest

from api.dso import contract
from tests.conftest import DATA_DIR, REPO_ROOT


def _files() -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(DATA_DIR.iterdir())}


def test_every_present_file_is_listed_and_matches():
    files = _files()
    sums = contract.parse_sha256sums(files["SHA256SUMS"])
    for name, content in files.items():
        if name == "SHA256SUMS":
            continue
        assert name in sums, f"{name} is not listed in SHA256SUMS"
        assert hashlib.sha256(content).hexdigest() == sums[name], f"{name} was edited"


def test_the_required_set_is_present_and_nothing_else():
    names = set(_files())
    assert set(contract.REQUIRED_FILES) <= names
    assert names <= set(contract.RELEASE_FILES)
    assert "REPORT.md" not in names, "sync_data.py does not keep REPORT.md"


def test_the_rule_as_the_loader_applies_it():
    contract.verify_integrity(_files())


def test_no_bom_no_cr_and_a_final_newline():
    for name, content in _files().items():
        assert not content.startswith(b"\xef\xbb\xbf"), f"{name} starts with a BOM"
        assert b"\r" not in content, f"{name} holds a CR (autocrlf?)"
        assert content.endswith(b"\n"), f"{name} has no final newline"


def test_one_data_version_equal_to_release_json():
    release = json.loads((DATA_DIR / "release.json").read_bytes())
    for name in ("dso_lookup.csv", "operators.csv", "notes.csv", "sources.csv"):
        header = {
            "dso_lookup.csv": contract.DSO_LOOKUP_HEADER,
            "operators.csv": contract.DIST_OPERATORS_HEADER,
            "notes.csv": contract.DIST_NOTES_HEADER,
            "sources.csv": contract.DIST_SOURCES_HEADER,
        }[name]
        rows = contract.parse_csv((DATA_DIR / name).read_bytes(), name=name, header=header)
        assert {row["data_version"] for row in rows} == {release["version"]}, name


def test_git_keeps_the_bytes():
    """`data/** -text`: without it, core.autocrlf=true on Windows checks the CSV files
    out with CRLF, and every hash above stops matching. The root ATTRIBUTION.md is
    compared byte for byte too (below): a CRLF checkout fails that test and makes
    every sync_data.py run report `changed=true`."""
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "data/** -text" in attributes
    assert "/ATTRIBUTION.md -text" in attributes


@pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(), reason="no git checkout"
)
def test_git_reads_the_attributes_as_meant():
    """What git itself resolves, so a mistyped pattern cannot satisfy the test above."""
    paths = ["ATTRIBUTION.md", "data/ATTRIBUTION.md", "data/dso_lookup.csv", "data/SHA256SUMS"]
    result = subprocess.run(  # noqa: S603  # fixed arguments
        ["git", "check-attr", "text", "--", *paths],  # noqa: S607  # git from PATH
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    resolved = dict(line.rsplit(": text: ", 1) for line in result.stdout.splitlines())
    assert resolved == dict.fromkeys(paths, "unset"), resolved


def test_git_does_not_ignore_data_or_scripts():
    """billing's .gitignore once swallowed its own scripts/ through an unanchored rule."""
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    rules = {line.strip() for line in ignore if line.strip() and not line.startswith("#")}
    for unanchored in ("[Ss]cripts", "[Ll]ib", "[Ll]ocal", "[Bb]in", "[Ii]nclude", "data"):
        assert unanchored not in rules, f"unanchored {unanchored!r} would ignore a directory"
    assert ".env.staging" in rules


def test_the_root_attribution_is_the_releases():
    """ATTRIBUTION.md = the CC BY 4.0 header + the pinned release's ATTRIBUTION.md.
    scripts/sync_data.py rewrites it with every release; a hand edit fails here. The
    header and the release must state the same licence: a release that changes it fails
    here (on its bump pull request) until the header follows. The repository is public:
    the file holds none of the project's internal planning names."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_data", REPO_ROOT / "scripts" / "sync_data.py"
    )
    assert spec is not None and spec.loader is not None
    sync_data = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sync_data)
    expected = sync_data.render_attribution((DATA_DIR / "ATTRIBUTION.md").read_bytes())
    assert (REPO_ROOT / "ATTRIBUTION.md").read_bytes() == expected

    def words(text: str) -> str:  # the licence sentence, whatever the line wrapping
        return " ".join(text.split())

    licence = (
        "licensed by OptimCE under the Creative Commons Attribution 4.0 International "
        "licence (CC BY 4.0), <https://creativecommons.org/licenses/by/4.0/>"
    )
    header = words(sync_data.ATTRIBUTION_HEADER)
    assert header.startswith(f"# Attribution **The compiled data in `data/` are {licence}.**")
    assert "Apache License 2.0 (`LICENSE`), which does not cover `data/`" in header
    assert "<https://github.com/OptimCE/belgian-dso-map>" in header
    release = words((DATA_DIR / "ATTRIBUTION.md").read_text(encoding="utf-8"))
    assert (
        f"The compiled dataset is {licence}." in release
    ), "the release no longer states CC BY 4.0: update sync_data.py's ATTRIBUTION_HEADER"
    assert b"not licensed" not in expected
    assert b"will be stated in a later" not in expected
    for internal in (b"Q5", b"lot C", b"**Draft"):
        assert internal not in expected, internal
