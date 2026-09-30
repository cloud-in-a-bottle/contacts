from collections.abc import Iterable
from xml.etree import ElementTree

import attr
from defusedxml.ElementTree import fromstring as defused_fromstring

DAV_NS = "DAV:"
CARDDAV_NS = "urn:ietf:params:xml:ns:carddav"
CALSERVER_NS = "http://calendarserver.org/ns/"

_PREFIXES = {"D": DAV_NS, "C": CARDDAV_NS, "CS": CALSERVER_NS}
for _prefix, _namespace in _PREFIXES.items():
    ElementTree.register_namespace(_prefix, _namespace)

STATUS_OK = "HTTP/1.1 200 OK"
STATUS_NOT_FOUND = "HTTP/1.1 404 Not Found"
STATUS_FORBIDDEN = "HTTP/1.1 403 Forbidden"


@attr.s(auto_attribs=True, frozen=True)
class PropName:
    namespace: str
    tag: str

    @classmethod
    def from_qualified(cls, qualified: str) -> "PropName":
        if qualified.startswith("{"):
            namespace, _, tag = qualified[1:].partition("}")
            return cls(namespace=namespace, tag=tag)
        return cls(namespace="", tag=qualified)

    @property
    def qualified(self) -> str:
        return f"{{{self.namespace}}}{self.tag}" if self.namespace else self.tag


@attr.s(auto_attribs=True, frozen=True)
class PropStatus:
    status: str
    properties: tuple[ElementTree.Element, ...]


@attr.s(auto_attribs=True, frozen=True)
class ResponseEntry:
    href: str
    propstats: tuple[PropStatus, ...] = ()
    status: str | None = None


def element(namespace: str, tag: str, text: str | None = None) -> ElementTree.Element:
    node = ElementTree.Element(f"{{{namespace}}}{tag}")
    if text is not None:
        node.text = text
    return node


def dav(tag: str, text: str | None = None) -> ElementTree.Element:
    return element(DAV_NS, tag, text)


def carddav(tag: str, text: str | None = None) -> ElementTree.Element:
    return element(CARDDAV_NS, tag, text)


def href_element(path: str) -> ElementTree.Element:
    return dav("href", path)


def with_children(parent: ElementTree.Element, children: Iterable[ElementTree.Element]) -> ElementTree.Element:
    for child in children:
        parent.append(child)
    return parent


def parse_request_body(body: bytes) -> ElementTree.Element | None:
    """Parse a request body as XML, returning None for an empty body and raising ValueError for malformed XML.

    defusedxml is used because these bodies arrive from the public internet: it refuses entity expansion and
    external-entity references, which a plain ElementTree parse would honour.
    """
    if not body.strip():
        return None
    try:
        parsed: ElementTree.Element = defused_fromstring(body)
    except Exception as error:  # noqa: BLE001 -- defusedxml raises several unrelated exception types
        raise ValueError(f"malformed XML request body: {error}") from error
    return parsed


def build_multistatus(entries: Iterable[ResponseEntry], sync_token: str | None = None) -> bytes:
    multistatus = dav("multistatus")
    for entry in entries:
        response = dav("response")
        response.append(href_element(entry.href))
        if entry.status is not None:
            response.append(dav("status", entry.status))
        for propstat in entry.propstats:
            node = dav("propstat")
            node.append(with_children(dav("prop"), propstat.properties))
            node.append(dav("status", propstat.status))
            response.append(node)
        multistatus.append(response)
    if sync_token is not None:
        multistatus.append(dav("sync-token", sync_token))
    return serialize(multistatus)


def serialize(root: ElementTree.Element) -> bytes:
    body: bytes = ElementTree.tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + body
