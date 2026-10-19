"""Draft data never reaches the public repository nor the image.

`belgian-dso-map build.py --draft` names its version `<v>-draft`. Developing
against a draft is normal (the mini fixture, then `.build/draft`), and
`scripts/sync_data.py --from-dir … --allow-draft` takes one. But this repository
and its `:main-prod` image are PUBLIC, and a draft has not been reviewed: under
CI (GitHub Actions sets `CI=true`) the data must be a release.

Skipped locally, where a draft is the expected state until the first release.
"""

import json
import os

import pytest

from tests.conftest import DATA_DIR


@pytest.mark.skipif(os.environ.get("CI") != "true", reason="CI only (CI=true)")
def test_ci_refuses_draft_data():
    version = json.loads((DATA_DIR / "release.json").read_bytes())["version"]
    assert not version.endswith("-draft"), (
        f"data/ holds the draft {version}. Re-sync a published release: "
        "python scripts/sync_data.py --version YYYY.MM.N"
    )
