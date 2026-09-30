import os
from pathlib import Path

import attr

# Local development escape hatch.  Outside a compute space there is no router to authenticate the owner, so the
# admin UI has nothing to gate on; setting this to "1" serves it to anyone who can reach the port.
DEV_NO_OWNER_AUTH_ENV = "CONTACTS_DEV_UNSAFE_NO_OWNER_AUTH"
# Explicit sqlite path for local runs.  In a compute space OPENHOST_SQLITE_MAIN is injected instead.
LOCAL_DB_PATH_ENV = "CONTACTS_DB_PATH"

OPENHOST_SQLITE_ENV = "OPENHOST_SQLITE_MAIN"


@attr.s(auto_attribs=True, frozen=True)
class Config:
    database_path: Path
    owner_username: str
    allow_unauthenticated_admin: bool


def load_config() -> Config:
    """Read configuration from the environment, failing loudly if the database has not been provisioned."""
    database_path = os.environ.get(OPENHOST_SQLITE_ENV) or os.environ.get(LOCAL_DB_PATH_ENV)
    if not database_path:
        raise RuntimeError(
            f"no database configured: expected ${OPENHOST_SQLITE_ENV} (set by OpenHost from the `sqlite` entry in "
            f"openhost.toml) or ${LOCAL_DB_PATH_ENV} for a local run"
        )
    return Config(
        database_path=Path(database_path),
        owner_username=os.environ.get("OPENHOST_OWNER_USERNAME") or "owner",
        allow_unauthenticated_admin=os.environ.get(DEV_NO_OWNER_AUTH_ENV) == "1",
    )
