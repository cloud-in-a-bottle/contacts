import base64
from collections.abc import Iterator
from pathlib import Path

import pytest
from litestar import Litestar
from litestar.testing import TestClient

from server.app import create_app
from server.config import Config
from server.credentials import CredentialStore
from server.db import Database
from server.store import ContactStore

OWNER_USERNAME = "alice"


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "contacts.db"


@pytest.fixture
def config(database_path: Path) -> Config:
    return Config(database_path=database_path, owner_username=OWNER_USERNAME, allow_unauthenticated_admin=False)


@pytest.fixture
def app(config: Config) -> Litestar:
    return create_app(config)


@pytest.fixture
def database(database_path: Path) -> Database:
    return Database(database_path)


@pytest.fixture
def store(database: Database) -> ContactStore:
    return ContactStore(database)


@pytest.fixture
def owner_client(app: Litestar) -> Iterator[TestClient[Litestar]]:
    """A client carrying the header the OpenHost router sets once it has authenticated the compute space owner."""
    with TestClient(app=app) as client:
        client.headers.update({"X-OpenHost-Is-Owner": "true"})
        yield client


@pytest.fixture
def anonymous_client(app: Litestar) -> Iterator[TestClient[Litestar]]:
    with TestClient(app=app) as client:
        yield client


@pytest.fixture
def carddav_password(app: Litestar, database_path: Path) -> str:
    # The app generates the password when it is built, so reading it back here is what the settings page shows.
    return CredentialStore(Database(database_path)).carddav_password().value


@pytest.fixture
def dav_client(app: Litestar, carddav_password: str) -> Iterator[TestClient[Litestar]]:
    credential = base64.b64encode(f"anyone:{carddav_password}".encode()).decode()
    with TestClient(app=app) as client:
        client.headers.update({"Authorization": f"Basic {credential}"})
        yield client
