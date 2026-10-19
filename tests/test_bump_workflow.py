"""The weekly data bump keeps running, and never opens a downgrade.

- GitHub disables the scheduled workflows of a public repository after 60 days
  without activity, silently (an email, no red check). Data releases come about
  every six months, so the cron alone goes quiet: belgian-dso-map's release.yml
  starts the bump with a `repository_dispatch` (`dso-data-released`), which the
  60-day rule does not touch.
- Every automatic run passes `--if-newer`: if GitHub's "latest" ever points at an
  older tag, the run is "up to date", never a "dso data X → older" pull request.
- Reading the release needs no secret: belgian-dso-map is public. The sync gets the
  run's automatic token (only against the anonymous rate limit); the PAT
  DSO_DATA_BOT_TOKEN pushes the branch and opens the pull request, because a pull
  request opened with the automatic token triggers no workflow.

PyYAML is not a dependency: the workflow is read as text, narrowly.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "dso-data-bump.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _triggers(text: str) -> dict[str, str]:
    """The top-level keys of `on:`, each with its indented block."""
    block = re.search(r"^on:\n((?:  .*\n|\n)+)", text, re.MULTILINE)
    assert block, "no `on:` block"
    triggers: dict[str, str] = {}
    current = ""
    for line in block.group(1).splitlines():
        key = re.fullmatch(r"  ([a-z_]+):.*", line)
        if key:
            current = key.group(1)
            triggers[current] = ""
        elif current:
            triggers[current] += line + "\n"
    return triggers


def test_a_release_dispatch_starts_the_bump():
    triggers = _triggers(_text())
    assert set(triggers) == {"repository_dispatch", "schedule", "workflow_dispatch"}
    assert re.search(r"types: \[dso-data-released\]", triggers["repository_dispatch"])
    assert "cron:" in triggers["schedule"]


def test_every_automatic_run_is_if_newer():
    text = _text()
    runs = re.findall(r"args=\(([^)]*)\)", text)
    assert runs == [
        '--version "${MANUAL_VERSION}"',
        '--version "${RELEASED_VERSION}" --if-newer',
        "--latest --if-newer",
    ], runs
    assert "RELEASED_VERSION: ${{ github.event.client_payload.version }}" in text
    # Expressions reach the shell through env only, never the script text.
    for run in re.findall(r"run: \|\n((?:          .*\n|\n)+)", text):
        assert "${{" not in run, run


def _step(text: str, name: str) -> str:
    """The body of the step `- name: <name>`, up to the next step or comment."""
    found = re.search(
        rf"^      - name: {re.escape(name)}\n((?:        .*\n|\n)*)", text, re.MULTILINE
    )
    assert found, f"no step {name!r}"
    return found.group(1)


def test_reading_the_release_needs_no_secret():
    text = _text()
    sync = _step(text, "Sync the release")
    assert "DSO_MAP_TOKEN: ${{ github.token }}" in sync
    assert "secrets." not in sync, "the sync reads a public repository: no secret"
    check = _step(text, "Check the token")
    assert "DSO_MAP_TOKEN" not in check, "nothing requires DSO_MAP_TOKEN"
    assert 'if [ -z "${DSO_DATA_BOT_TOKEN}" ]; then' in check
    assert set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text)) == {"DSO_DATA_BOT_TOKEN"}


def test_the_pat_pushes_and_opens_the_pull_request():
    text = _text()
    checkout = re.search(
        r"^      - uses: actions/checkout@v[0-9]+\n((?:        .*\n)*)", text, re.MULTILINE
    )
    assert checkout, "no checkout step"
    assert "token: ${{ secrets.DSO_DATA_BOT_TOKEN }}" in checkout.group(1)
    pull_request = _step(text, "Open the pull request")
    assert "GH_TOKEN: ${{ secrets.DSO_DATA_BOT_TOKEN }}" in pull_request
    assert "github.token" not in pull_request
    assert "gh pr create" in pull_request
