from xml.etree import ElementTree

from server.credentials import CredentialStore
from server.dav.auth import is_authorized
from server.dav.messages import DavRequest
from server.dav.messages import DavResponse
from server.dav.paths import ADDRESSBOOK_PATH
from server.dav.paths import DAV_ROOT
from server.dav.paths import WELL_KNOWN_PATH
from server.dav.paths import Resource
from server.dav.paths import ResourceKind
from server.dav.paths import contact_path
from server.dav.paths import resolve
from server.dav.paths import static_children
from server.dav.propfind import Target
from server.dav.propfind import parse_propfind
from server.dav.propfind import response_entries
from server.dav.props import MAX_RESOURCE_SIZE
from server.dav.props import VCARD_CONTENT_TYPE
from server.dav.props import PropertyContext
from server.dav.report import MULTISTATUS_CONTENT_TYPE
from server.dav.report import handle_report
from server.dav.xml import CARDDAV_NS
from server.dav.xml import DAV_NS
from server.dav.xml import STATUS_FORBIDDEN
from server.dav.xml import PropStatus
from server.dav.xml import ResponseEntry
from server.dav.xml import build_multistatus
from server.dav.xml import dav
from server.dav.xml import element
from server.dav.xml import parse_request_body
from server.dav.xml import serialize
from server.dav.xml import with_children
from server.models import Contact
from server.store import ContactStore
from server.store import describe
from server.vcard.summary import summarize

DAV_COMPLIANCE = "1, 3, access-control, addressbook"
AUTH_REALM = 'Basic realm="contacts", charset="UTF-8"'

_COLLECTION_METHODS = "OPTIONS, GET, HEAD, PROPFIND, PROPPATCH, REPORT"
_CONTACT_METHODS = "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, PROPPATCH, REPORT"
_ACCEPTED_PUT_TYPES = ("text/vcard", "text/x-vcard", "text/directory", "text/plain", "application/octet-stream")


def _error(status: int, namespace: str, tag: str) -> DavResponse:
    body = serialize(with_children(dav("error"), (element(namespace, tag),)))
    return DavResponse(status=status, body=body, content_type=MULTISTATUS_CONTENT_TYPE)


def _plain(status: int, message: str, headers: tuple[tuple[str, str], ...] = ()) -> DavResponse:
    return DavResponse(
        status=status, body=message.encode("utf-8"), content_type="text/plain; charset=utf-8", headers=headers
    )


def _etag_candidates(header_value: str) -> list[str]:
    return [part.strip().removeprefix("W/").strip() for part in header_value.split(",") if part.strip()]


def _allow_header(resource: Resource) -> tuple[tuple[str, str], ...]:
    return (("Allow", _CONTACT_METHODS if resource.kind is ResourceKind.CONTACT else _COLLECTION_METHODS),)


class DavHandler:
    """The CardDAV endpoint, as a pure request-in / response-out object.

    Everything here is synchronous; the ASGI adapter runs it on a worker thread so the sqlite calls never block the
    event loop.
    """

    def __init__(self, store: ContactStore, credentials: CredentialStore, owner_username: str) -> None:
        self._store = store
        self._credentials = credentials
        self._owner_username = owner_username

    def gate(self, request: DavRequest) -> DavResponse | None:
        """The answer to give before the body is even read, or None if the request may proceed.

        This looks only at the method, path and headers, so the ASGI adapter can call it first and never buffer a
        body for someone who has not presented the password.  Everything an unauthenticated client can reach is
        decided here.
        """
        if request.path.startswith(WELL_KNOWN_PATH):
            # RFC 6764 bootstrapping: point the client at the context path and let it discover the rest.  No
            # authentication, because this reveals nothing beyond the URL layout.
            return DavResponse(status=301, headers=(("Location", f"{DAV_ROOT}/"),))

        if not request.is_router_owner and not is_authorized(request.header("authorization"), self._credentials):
            return DavResponse(
                status=401,
                body=b"CardDAV authentication required",
                content_type="text/plain; charset=utf-8",
                headers=(("WWW-Authenticate", AUTH_REALM),),
            )
        return None

    def handle(self, request: DavRequest) -> DavResponse:
        if (refusal := self.gate(request)) is not None:
            return refusal

        resource = resolve(request.path)
        if resource is None:
            return _plain(404, "no such CardDAV resource")

        # Resolve the contact once, here: a missing one is a 404 for every method except the two that do not
        # need it to exist, and the methods that do need it should not each go back to the store for it.
        contact = self._store.get(resource.resource_name) if resource.kind is ResourceKind.CONTACT else None
        if resource.kind is ResourceKind.CONTACT and contact is None and request.method not in ("PUT", "OPTIONS"):
            return _plain(404, "no such contact")

        response = self._dispatch(request, resource, contact)
        return DavResponse(
            status=response.status,
            body=response.body,
            content_type=response.content_type,
            headers=(*response.headers, ("DAV", DAV_COMPLIANCE)),
        )

    def _dispatch(self, request: DavRequest, resource: Resource, contact: Contact | None) -> DavResponse:
        method = request.method.upper()
        if method == "OPTIONS":
            return DavResponse(status=200, headers=_allow_header(resource))
        if method == "PROPFIND":
            return self._propfind(request, resource, contact)
        if method == "PROPPATCH":
            return self._proppatch(request, resource)
        if method == "REPORT":
            return handle_report(request.body, resource, self._store, self._context())
        if method in ("GET", "HEAD"):
            return self._get(request, resource, contact, include_body=method == "GET")
        if method == "PUT":
            return self._put(request, resource, contact)
        if method == "DELETE":
            return self._delete(request, resource, contact)
        return _plain(405, f"{method} is not supported here", _allow_header(resource))

    def _context(self) -> PropertyContext:
        return PropertyContext(token=self._store.token(), owner_username=self._owner_username)

    def _targets(self, resource: Resource, depth: str, contact: Contact | None = None) -> tuple[Target, ...]:
        targets = [Target(resource=resource, contact=contact)]
        if depth == "0" or resource.kind is ResourceKind.CONTACT:
            return tuple(targets)

        for child in static_children(resource):
            targets.append(Target(resource=child))
            if depth == "infinity":
                targets.extend(self._targets(child, depth)[1:])
        if resource.kind is ResourceKind.ADDRESSBOOK:
            targets.extend(
                Target(
                    resource=Resource(
                        kind=ResourceKind.CONTACT,
                        path=contact_path(contact.resource_name),
                        resource_name=contact.resource_name,
                    ),
                    contact=contact,
                )
                for contact in self._store.list_contacts()
            )
        return tuple(targets)

    def _propfind(self, request: DavRequest, resource: Resource, contact: Contact | None) -> DavResponse:
        try:
            query = parse_propfind(request.body)
        except ValueError as error:
            return _plain(400, str(error))
        depth = request.depth if request.depth in ("0", "1", "infinity") else "0"
        entries = response_entries(self._targets(resource, depth, contact), query, self._context())
        return DavResponse(status=207, body=build_multistatus(entries), content_type=MULTISTATUS_CONTENT_TYPE)

    def _proppatch(self, request: DavRequest, resource: Resource) -> DavResponse:
        """Accept the request but change nothing: every property here is live and derived from the contact data."""
        try:
            root = parse_request_body(request.body)
        except ValueError as error:
            return _plain(400, str(error))
        names: list[ElementTree.Element] = []
        if root is not None:
            for action in (f"{{{DAV_NS}}}set", f"{{{DAV_NS}}}remove"):
                for container in root.findall(action):
                    for prop_node in container.findall(f"{{{DAV_NS}}}prop"):
                        names.extend(ElementTree.Element(child.tag) for child in prop_node)
        entry = ResponseEntry(
            href=resource.path, propstats=(PropStatus(status=STATUS_FORBIDDEN, properties=tuple(names)),)
        )
        return DavResponse(status=207, body=build_multistatus((entry,)), content_type=MULTISTATUS_CONTENT_TYPE)

    def _get(
        self, request: DavRequest, resource: Resource, contact: Contact | None, include_body: bool
    ) -> DavResponse:
        if resource.kind is not ResourceKind.CONTACT:
            body = (
                "<!doctype html><meta charset='utf-8'><title>CardDAV</title>"
                "<h1>CardDAV endpoint</h1>"
                f"<p>Point a CardDAV client at <code>{ADDRESSBOOK_PATH}</code> on this host.</p>"
            ).encode()
            return DavResponse(status=200, body=body if include_body else b"", content_type="text/html; charset=utf-8")

        assert contact is not None, "a missing contact is answered before dispatch"
        if contact.etag in _etag_candidates(request.header("if-none-match")):
            return DavResponse(status=304, headers=(("ETag", contact.etag),))
        encoded = contact.vcard.encode("utf-8")
        return DavResponse(
            status=200,
            body=encoded if include_body else b"",
            content_type=VCARD_CONTENT_TYPE,
            headers=(("ETag", contact.etag), ("Content-Length", str(len(encoded)))),
        )

    def _put(self, request: DavRequest, resource: Resource, existing: Contact | None) -> DavResponse:
        if resource.kind is not ResourceKind.CONTACT:
            return _plain(405, "contacts can only be written inside the address book", _allow_header(resource))

        content_type = request.header("content-type").split(";")[0].strip().lower()
        if content_type and content_type not in _ACCEPTED_PUT_TYPES:
            return _error(415, CARDDAV_NS, "supported-address-data")
        if len(request.body) > MAX_RESOURCE_SIZE:
            return _error(413, CARDDAV_NS, "max-resource-size")

        try:
            text = request.body.decode("utf-8")
        except UnicodeDecodeError:
            return _error(400, CARDDAV_NS, "valid-address-data")
        if text.upper().count("BEGIN:VCARD") != 1 or "END:VCARD" not in text.upper():
            return _error(400, CARDDAV_NS, "valid-address-data")

        if "*" in _etag_candidates(request.header("if-none-match")) and existing is not None:
            return _plain(412, "a contact already exists at this address")
        if if_match := request.header("if-match"):
            candidates = _etag_candidates(if_match)
            if existing is None:
                return _plain(412, "no contact exists at this address")
            if "*" not in candidates and existing.etag not in candidates:
                return _plain(412, "the contact has changed since it was read")

        result = self._store.put(
            resource.resource_name, text, describe("carddav put", _subject(text, resource.resource_name))
        )
        return DavResponse(
            status=201 if result.was_created else 204,
            headers=(("ETag", result.contact.etag), ("Location", resource.path)),
        )

    def _delete(self, request: DavRequest, resource: Resource, existing: Contact | None) -> DavResponse:
        if resource.kind is not ResourceKind.CONTACT:
            return _plain(405, "the address book itself cannot be deleted", _allow_header(resource))
        assert existing is not None, "a missing contact is answered before dispatch"
        if if_match := request.header("if-match"):
            candidates = _etag_candidates(if_match)
            if "*" not in candidates and existing.etag not in candidates:
                return _plain(412, "the contact has changed since it was read")
        self._store.delete(resource.resource_name, describe("carddav delete", existing.display_name))
        return DavResponse(status=204)


def _subject(vcard_text: str, resource_name: str) -> str:
    """What to call this card in the commit log, before it has been stored."""
    return summarize(vcard_text, fallback_uid=resource_name).display_name
