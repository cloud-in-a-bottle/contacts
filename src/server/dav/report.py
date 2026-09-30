from xml.etree import ElementTree

from server.dav.filters import matches_filter
from server.dav.messages import DavResponse
from server.dav.paths import Resource
from server.dav.paths import ResourceKind
from server.dav.paths import contact_path
from server.dav.paths import resource_name_from_href
from server.dav.propfind import PropfindMode
from server.dav.propfind import PropfindQuery
from server.dav.propfind import Target
from server.dav.propfind import parse_propfind
from server.dav.propfind import response_entry
from server.dav.props import PropertyContext
from server.dav.tokens import parse_sync_token
from server.dav.tokens import sync_token
from server.dav.xml import CARDDAV_NS
from server.dav.xml import DAV_NS
from server.dav.xml import STATUS_NOT_FOUND
from server.dav.xml import ResponseEntry
from server.dav.xml import build_multistatus
from server.dav.xml import dav
from server.dav.xml import parse_request_body
from server.dav.xml import serialize
from server.dav.xml import with_children
from server.models import Contact
from server.store import ContactStore

MULTISTATUS_CONTENT_TYPE = "application/xml; charset=utf-8"


def _multistatus(entries: tuple[ResponseEntry, ...], token: str | None = None) -> DavResponse:
    return DavResponse(
        status=207, body=build_multistatus(entries, sync_token=token), content_type=MULTISTATUS_CONTENT_TYPE
    )


def _query_from(node: ElementTree.Element | None) -> PropfindQuery:
    """Reuse the PROPFIND body parser for the ``<D:prop>`` element a REPORT carries."""
    if node is None:
        return PropfindQuery(mode=PropfindMode.ALLPROP)
    wrapper = dav("propfind")
    wrapper.append(node)
    return parse_propfind(serialize(wrapper))


def handle_report(body: bytes, resource: Resource, store: ContactStore, context: PropertyContext) -> DavResponse:
    try:
        root = parse_request_body(body)
    except ValueError:
        return DavResponse(status=400, body=b"malformed XML body", content_type="text/plain; charset=utf-8")
    if root is None:
        return DavResponse(status=400, body=b"a REPORT requires a body", content_type="text/plain; charset=utf-8")

    if root.tag == f"{{{CARDDAV_NS}}}addressbook-multiget":
        return _addressbook_multiget(root, store, context)
    if root.tag == f"{{{CARDDAV_NS}}}addressbook-query":
        return _addressbook_query(root, resource, store, context)
    if root.tag == f"{{{DAV_NS}}}sync-collection":
        return _sync_collection(root, resource, store, context)
    if root.tag == f"{{{DAV_NS}}}principal-property-search":
        # We have exactly one principal and no directory to search; an empty result is the honest answer.
        return _multistatus(())
    if root.tag == f"{{{DAV_NS}}}principal-search-property-set":
        return DavResponse(
            status=200,
            body=serialize(dav("principal-search-property-set")),
            content_type=MULTISTATUS_CONTENT_TYPE,
        )
    return DavResponse(
        status=403,
        body=serialize(with_children(dav("error"), (dav("supported-report"),))),
        content_type=MULTISTATUS_CONTENT_TYPE,
    )


def _contact_entry(contact: Contact, query: PropfindQuery, context: PropertyContext) -> ResponseEntry:
    resource = Resource(
        kind=ResourceKind.CONTACT, path=contact_path(contact.resource_name), resource_name=contact.resource_name
    )
    return response_entry(Target(resource=resource, contact=contact), query, context)


def _addressbook_multiget(root: ElementTree.Element, store: ContactStore, context: PropertyContext) -> DavResponse:
    query = _query_from(root.find(f"{{{DAV_NS}}}prop"))
    entries: list[ResponseEntry] = []
    for href_node in root.findall(f"{{{DAV_NS}}}href"):
        href = (href_node.text or "").strip()
        resource_name = resource_name_from_href(href)
        contact = store.get(resource_name) if resource_name else None
        if contact is None:
            entries.append(ResponseEntry(href=href, status=STATUS_NOT_FOUND))
            continue
        entries.append(_contact_entry(contact, query, context))
    return _multistatus(tuple(entries))


def _addressbook_query(
    root: ElementTree.Element, resource: Resource, store: ContactStore, context: PropertyContext
) -> DavResponse:
    if resource.kind is not ResourceKind.ADDRESSBOOK:
        return DavResponse(status=403, body=b"addressbook-query only applies to the address book")
    query = _query_from(root.find(f"{{{DAV_NS}}}prop"))
    filter_node = root.find(f"{{{CARDDAV_NS}}}filter")
    matches = [contact for contact in store.list_contacts() if matches_filter(filter_node, contact.vcard)]
    if (limit_node := root.find(f"{{{CARDDAV_NS}}}limit")) is not None:
        results_node = limit_node.find(f"{{{CARDDAV_NS}}}nresults")
        if results_node is not None and (results_node.text or "").strip().isdigit():
            matches = matches[: int((results_node.text or "").strip())]
    return _multistatus(tuple(_contact_entry(contact, query, context) for contact in matches))


def _sync_collection(
    root: ElementTree.Element, resource: Resource, store: ContactStore, context: PropertyContext
) -> DavResponse:
    if resource.kind is not ResourceKind.ADDRESSBOOK:
        return DavResponse(status=403, body=b"sync-collection only applies to the address book")

    token_node = root.find(f"{{{DAV_NS}}}sync-token")
    since_seq = parse_sync_token(token_node.text or "" if token_node is not None else "")
    if since_seq is None:
        return DavResponse(
            status=409,
            body=serialize(with_children(dav("error"), (dav("valid-sync-token"),))),
            content_type=MULTISTATUS_CONTENT_TYPE,
        )

    query = _query_from(root.find(f"{{{DAV_NS}}}prop"))
    delta = store.changes_since(since_seq)
    entries: list[ResponseEntry] = [_contact_entry(contact, query, context) for contact in delta.changed]
    entries.extend(
        ResponseEntry(href=contact_path(resource_name), status=STATUS_NOT_FOUND) for resource_name in delta.deleted
    )
    return _multistatus(tuple(entries), token=sync_token(delta.change_seq))
