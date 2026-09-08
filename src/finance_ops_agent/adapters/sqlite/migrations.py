"""Apply Alembic migrations programmatically (also used by `fops` at start-up)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine

_SCRIPT_LOCATION = Path(__file__).parent / "alembic"


def upgrade_to_head(engine: Engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(_SCRIPT_LOCATION))
    config.attributes["engine"] = engine
    command.upgrade(config, "head")
