from collections.abc import Mapping

import attr


@attr.s(auto_attribs=True, frozen=True)
class DavRequest:
    """A CardDAV request, decoupled from ASGI so the protocol layer is plain synchronous code."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes
    is_router_owner: bool

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    @property
    def depth(self) -> str:
        """The Depth header, defaulting to "0" — the safe reading when a client omits it."""
        return self.header("depth", "0").strip().lower() or "0"


@attr.s(auto_attribs=True, frozen=True)
class DavResponse:
    status: int
    body: bytes = b""
    content_type: str = ""
    headers: tuple[tuple[str, str], ...] = ()
