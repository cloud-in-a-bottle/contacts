import attr
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
from server.dav.props import MAX_RESOURCE_SIZE

OWNER_HEADER = "x-openhost-is-owner"
_BODYLESS_STATUSES = frozenset({204, 304})
# Nothing else bounds what we buffer: hypercorn has no body limit.  The headroom over a single card's limit is so a
# PUT just over it still reaches the handler and gets the CardDAV max-resource-size error a client understands.
MAX_REQUEST_BODY = MAX_RESOURCE_SIZE + 1024 * 1024


class _BodyTooLarge(Exception):
    pass


def _declared_length(headers: dict[str, str]) -> int | None:
    value = headers.get("content-length", "").strip()
    return int(value) if value.isdigit() else None


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        if message["type"] == "http.request":
            if chunk := message.get("body"):
                size += len(chunk)
                if size > MAX_REQUEST_BODY:
                    raise _BodyTooLarge()
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
        head = DavRequest(
            method=str(http_scope["method"]).upper(),
            path=_original_path(http_scope),
            headers=headers,
            body=b"",
            is_router_owner=headers.get(OWNER_HEADER, "").lower() == "true",
        )
        try:
            # The password is checked before the body is read, so an unauthenticated client cannot make us buffer
            # anything.  The protocol layer is synchronous and talks to sqlite, so it runs off the event loop.
            if (refusal := await run_sync(handler.gate, head)) is not None:
                await _send_response(send, refusal)
                return
            declared = _declared_length(headers)
            if declared is not None and declared > MAX_REQUEST_BODY:
                raise _BodyTooLarge()
            request = attr.evolve(head, body=await _read_body(receive))
            response = await run_sync(handler.handle, request)
        except _BodyTooLarge:
            response = DavResponse(
                status=413, body=b"request body too large", content_type="text/plain; charset=utf-8"
            )
        except Exception:
            logger.opt(exception=True).error("CardDAV request failed: {} {}", head.method, head.path)
            response = DavResponse(status=500, body=b"internal error", content_type="text/plain; charset=utf-8")
        await _send_response(send, response)

    return dav_asgi
