from pathlib import Path

import attr
from litestar import Litestar
from litestar import asgi
from litestar import get
from litestar.di import Provide
from litestar.plugins.jinja import JinjaTemplateEngine
from litestar.template.config import TemplateConfig
from loguru import logger

from server.config import Config
from server.config import load_config
from server.credentials import CredentialStore
from server.dav.asgi import make_dav_asgi
from server.dav.handler import DavHandler
from server.dav.paths import DAV_ROOT
from server.dav.paths import WELL_KNOWN_PATH
from server.db import Database
from server.repo import Repository
from server.store import ContactStore
from server.web.admin import admin_router
from server.web.auth import owner_guard

TEMPLATE_DIRECTORY = Path(__file__).parent / "web" / "templates"


@attr.s(auto_attribs=True, frozen=True)
class HealthStatus:
    status: str


@get("/health", sync_to_thread=False)
def health() -> HealthStatus:
    return HealthStatus(status="ok")


def create_app(config: Config | None = None) -> Litestar:
    resolved = config if config is not None else load_config()
    database = Database(resolved.database_path)
    repository = Repository(resolved.repository_path)
    store = ContactStore(repository)
    credentials = CredentialStore(database)
    # Materialise the CardDAV password now so that it exists from the app's first boot rather than from the first
    # time someone opens the settings page.
    credentials.carddav_password()

    dav_asgi = make_dav_asgi(DavHandler(store, credentials, resolved.owner_username))

    def provide_store() -> ContactStore:
        return store

    def provide_credentials() -> CredentialStore:
        return credentials

    def provide_owner_username() -> str:
        return resolved.owner_username

    if resolved.allow_unauthenticated_admin:
        logger.warning(
            "{} is set: the admin UI is being served without checking for the compute space owner",
            "CONTACTS_DEV_UNSAFE_NO_OWNER_AUTH",
        )
    logger.info("contacts starting: address book at {}, app state at {}", repository.path, database.path)

    return Litestar(
        route_handlers=[
            health,
            asgi(DAV_ROOT, is_mount=True, copy_scope=True)(dav_asgi),
            asgi(WELL_KNOWN_PATH, is_mount=True, copy_scope=True)(dav_asgi),
            admin_router(owner_guard(resolved.allow_unauthenticated_admin)),
        ],
        dependencies={
            "store": Provide(provide_store, sync_to_thread=False),
            "credentials": Provide(provide_credentials, sync_to_thread=False),
            "owner_username": Provide(provide_owner_username, sync_to_thread=False),
        },
        template_config=TemplateConfig(directory=TEMPLATE_DIRECTORY, engine=JinjaTemplateEngine),
        # Litestar would otherwise serve its generated API docs under /schema, outside the owner guard.  Nothing
        # here is an API for anyone else to call, so there is no reason to describe it to them.
        openapi_config=None,
    )
