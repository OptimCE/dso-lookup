import os
from enum import StrEnum
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# dso-lookup is INTERNAL: crm-backend is its only caller, over the Docker network.
# So, unlike the annexes it is modelled on (billing), there is no ALLOW_ORIGIN /
# CORS contract, no gateway headers and no database URL to require here.

# The pinned data release ships inside the image, next to the code.
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = REPO_ROOT / "data"


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


def _get_env_file() -> str:
    env = os.getenv("ENV", "local").strip()
    return f".env.{env}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_get_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ---- Core ----
    ENV: Environment = Environment.LOCAL

    # ---- Data ----
    # The belgian-dso-map release this service answers from. Empty means the
    # repository's own data/ (what the image ships). It is written only by
    # scripts/sync_data.py; a directory that fails the strict checks of
    # api/dso/store.py fails the boot.
    DSO_DATA_DIR: str = ""
    # True ONLY to serve a fixture build that is not a draft (belgian-dso-map
    # `build.py --fixture --release --out ...`): readiness then skips the
    # national counts (565 communes = 261 WAL / 285 VLA / 19 BRU). Never in
    # production, where the validator below refuses it. A draft (`-draft`) never
    # asserts the national counts anyway.
    DSO_DATA_FIXTURE: bool = False

    LOGGING_TOKEN: str = ""
    LOGGING_TRACES_URL: str = ""
    LOGGING_LOGS_URL: str = ""
    LOGGING_METRICS_URL: str = ""

    # ---- Localization ----
    DEFAULT_LOCALE: str = "fr-BE"

    @property
    def data_dir(self) -> Path:
        return Path(self.DSO_DATA_DIR) if self.DSO_DATA_DIR.strip() else DEFAULT_DATA_DIR

    @model_validator(mode="after")
    def validate_env_config(self) -> "Settings":
        if self.ENV == Environment.PRODUCTION:
            if not self.LOGGING_TOKEN:
                raise ValueError("LOGGING_TOKEN required for staging/production")
            if not self.LOGGING_LOGS_URL:
                raise ValueError("LOGGING_LOGS_URL required for staging/production")
            if not self.LOGGING_METRICS_URL:
                raise ValueError("LOGGING_METRICS_URL required for staging/production")
            if self.DSO_DATA_FIXTURE:
                raise ValueError("DSO_DATA_FIXTURE is a test switch; never set it in production")
        return self


settings = Settings()
