from typing import Any

from litestar import Request


def external_origin(request: Request[Any, Any, Any]) -> str:
    """The scheme and host a browser reached this app on.

    The OpenHost router rewrites Host to the public name and sets X-Forwarded-Proto, so this yields the URL an
    external CardDAV client should be given rather than the container's internal address.
    """
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{scheme}://{host}"
