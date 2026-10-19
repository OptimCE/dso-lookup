"""Global test fixtures for dso-lookup.

No database, no broker: the service is its data release (`data/`) plus pure
functions. The HTTP client runs INSIDE `app.router.lifespan_context(app)`,
because httpx's ASGITransport does not run the lifespan, and the lifespan is
what loads (and strictly checks) `data/`. Every HTTP test therefore exercises
the real loader on the real files.
"""

import hashlib
import os
import shutil
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

# Force the test environment file (.env.test) BEFORE any project module is
# imported. core.config.Settings reads ENV at import time to choose .env.<env>,
# so this assignment must happen before `from main import app` below.
os.environ.setdefault("ENV", "test")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.dso import contract
from api.dso.store import DsoStore, load_store
from core.config import DEFAULT_DATA_DIR
from main import app

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = DEFAULT_DATA_DIR


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Full HTTP stack on the real data/, lifespan included."""
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        yield ac


@pytest.fixture(scope="session")
def store() -> DsoStore:
    return load_store(DATA_DIR)


@pytest.fixture(scope="session")
def vectors() -> list[dict[str, str]]:
    return contract.parse_csv(
        (DATA_DIR / "vectors.csv").read_bytes(),
        name="vectors.csv",
        header=contract.VECTORS_HEADER,
        raw_columns=contract.VECTOR_INPUT_COLUMNS,
    )


def write_sums(directory: Path) -> None:
    """Rewrite SHA256SUMS over the files of `directory`, the way belgian-dso-map
    build.py writes it, so a test can break ONE rule and keep the others."""
    names = sorted(p.name for p in directory.iterdir() if p.name != contract.SHA256SUMS)
    lines = [
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n" for name in names
    ]
    (directory / contract.SHA256SUMS).write_bytes("".join(lines).encode("utf-8"))


@pytest.fixture
def release_copy(tmp_path: Path) -> Path:
    """A private copy of data/ a test may break."""
    target = tmp_path / "release"
    shutil.copytree(DATA_DIR, target)
    return target


@pytest.fixture
def edit_release(release_copy: Path) -> Callable[..., Path]:
    """`edit_release("dso_lookup.csv", lambda text: ...)`: rewrite one file of the
    copy (as text, LF kept) and re-sum it, unless `resum=False`."""

    def _edit(name: str, change: Callable[[str], str], *, resum: bool = True) -> Path:
        path = release_copy / name
        text = path.read_bytes().decode("utf-8")
        path.write_bytes(change(text).encode("utf-8"))
        if resum:
            write_sums(release_copy)
        return release_copy

    return _edit
