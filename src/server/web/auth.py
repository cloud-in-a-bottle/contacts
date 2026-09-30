from collections.abc import Callable
from typing import Any

from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers.base import BaseRouteHandler

# Set by the OpenHost router once it has verified the compute space owner's session.  The router strips any
# inbound X-OpenHost-* header before proxying, so a client cannot forge this.
OWNER_HEADER = "x-openhost-is-owner"


def owner_guard(
    allow_unauthenticated: bool,
) -> Callable[[ASGIConnection[Any, Any, Any, Any], BaseRouteHandler], None]:
    """Guard the admin UI.

    In a compute space the router has already authenticated the owner before the request reaches us; this checks
    the header it sets, so the app never serves the UI on a path the router left open.
    """

    def guard(connection: ASGIConnection[Any, Any, Any, Any], _: BaseRouteHandler) -> None:
        if connection.headers.get(OWNER_HEADER, "").lower() == "true":
            return
        if allow_unauthenticated:
            return
        raise NotAuthorizedException(detail="this page is only available to the owner of this compute space")

    return guard
