from anyio.to_thread import run_sync
from litestar.enums import ScopeType
from litestar.types import ASGIApp
from litestar.types import Receive
from litestar.types import Scope
from litestar.types import Send
from litestar.types.asgi_types import HTTPResponseBodyEvent
from litestar.types.asgi_types import HTTPResponseStartEvent
from litestar.types.asgi_types import HTTPScope
from litestar.types.asgi_types import WebSocketCloseEvent
from loguru import logger

from server.dav.handler import DavHandler
from server.dav.messages import DavRequest
from server.dav.messages import DavResponse

OWNER_HEADER = "x-openhost-is-owner"
_BODYLESS_STATUSES = frozenset({204, 304})


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        if message["type"] == "http.request":
            if chunk := message.get("body"):
                chunks.append(chunk)
            if not message.get("more_body", False):
                break
    return b"".join(chunks)


def _original_path(scope: HTTPScope) -> str:
    """The path as the client sent it.

    Litestar rewrites ``scope["path"]`` to the portion after a mount point (and appends a trailing slash), so the
    raw path is the only faithful source.  Every ASGI server we run under sets it.
    """
    raw_path = scope.get("raw_path")
    if raw_path is None:
        raise RuntimeError("the ASGI server did not provide raw_path, which CardDAV path resolution requires")
    return raw_path.decode("latin-1").split("?", 1)[0]


def _headers(scope: HTTPScope) -> dict[str, str]:
    return {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope["headers"]}


async def _send_response(send: Send, response: DavResponse) -> None:
    body = b"" if response.status in _BODYLESS_STATUSES else response.body
    headers = [(name.encode("latin-1"), value.encode("latin-1")) for name, value in response.headers]
    if response.status not in _BODYLESS_STATUSES:
        if body or response.content_type:
            headers.append((b"content-type", (response.content_type or "application/octet-stream").encode("latin-1")))
        if not any(name == b"content-length" for name, _ in headers):
            headers.append((b"content-length", str(len(body)).encode("latin-1")))
    start: HTTPResponseStartEvent = {
        "type": "http.response.start",
        "status": response.status,
        "headers": headers,
    }
    payload: HTTPResponseBodyEvent = {"type": "http.response.body", "body": body, "more_body": False}
    await send(start)
    await send(payload)


def make_dav_asgi(handler: DavHandler) -> ASGIApp:
    async def dav_asgi(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != ScopeType.HTTP:
            # CardDAV is HTTP-only; a websocket that lands here is a client error, not something to upgrade.
            close: WebSocketCloseEvent = {
                "type": "websocket.close",
                "code": 4400,
                "reason": "the CardDAV endpoint does not speak websockets",
            }
            await send(close)
            return

        http_scope: HTTPScope = scope
        headers = _headers(http_scope)
        request = DavRequest(
            method=str(http_scope["method"]).upper(),
            path=_original_path(http_scope),
            headers=headers,
            body=await _read_body(receive),
            is_router_owner=headers.get(OWNER_HEADER, "").lower() == "true",
        )
        try:
            # The protocol layer is synchronous and talks to sqlite, so it runs off the event loop.
            response = await run_sync(handler.handle, request)
        except Exception:
            logger.opt(exception=True).error("CardDAV request failed: {} {}", request.method, request.path)
            response = DavResponse(status=500, body=b"internal error", content_type="text/plain; charset=utf-8")
        await _send_response(send, response)

    return dav_asgi
