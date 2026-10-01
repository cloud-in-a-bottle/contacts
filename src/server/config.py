import os
from pathlib import Path

import attr

# Local development escape hatch.  Outside a compute space there is no router to authenticate the owner, so the
# admin UI has nothing to gate on; setting this to "1" serves it to anyone who can reach the port.
DEV_NO_OWNER_AUTH_ENV = "CONTACTS_DEV_UNSAFE_NO_OWNER_AUTH"
# Explicit paths for local runs.  In a compute space OpenHost injects the OPENHOST_* pair instead.
LOCAL_DB_PATH_ENV = "CONTACTS_DB_PATH"
LOCAL_REPO_PATH_ENV = "CONTACTS_REPO_PATH"

OPENHOST_SQLITE_ENV = "OPENHOST_SQLITE_MAIN"
OPENHOST_APP_DATA_ENV = "OPENHOST_APP_DATA_DIR"

# Sits beside the "sqlite" directory OpenHost provisions inside the app's data directory.
REPOSITORY_DIRECTORY_NAME = "addressbook"


@attr.s(auto_attribs=True, frozen=True)
class Config:
    database_path: Path
    repository_path: Path
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
    repository_path = os.environ.get(LOCAL_REPO_PATH_ENV)
    if not repository_path:
        app_data = os.environ.get(OPENHOST_APP_DATA_ENV)
        if not app_data:
            raise RuntimeError(
                f"no address book location configured: expected ${OPENHOST_APP_DATA_ENV} (set by OpenHost when "
                f"the manifest requests app data) or ${LOCAL_REPO_PATH_ENV} for a local run"
            )
        repository_path = str(Path(app_data) / REPOSITORY_DIRECTORY_NAME)

    return Config(
        database_path=Path(database_path),
        repository_path=Path(repository_path),
        owner_username=os.environ.get("OPENHOST_OWNER_USERNAME") or "owner",
        allow_unauthenticated_admin=os.environ.get(DEV_NO_OWNER_AUTH_ENV) == "1",
    )
