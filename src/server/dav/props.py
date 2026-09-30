from collections.abc import Callable
from collections.abc import Mapping
from datetime import datetime
from email.utils import format_datetime
from xml.etree import ElementTree

import attr

from server.dav.paths import HOME_PATH
from server.dav.paths import PRINCIPAL_PATH
from server.dav.paths import Resource
from server.dav.paths import ResourceKind
from server.dav.tokens import sync_token
from server.dav.xml import CALSERVER_NS
from server.dav.xml import CARDDAV_NS
from server.dav.xml import DAV_NS
from server.dav.xml import PropName
from server.dav.xml import carddav
from server.dav.xml import dav
from server.dav.xml import element
from server.dav.xml import href_element
from server.dav.xml import with_children
from server.models import Contact

ADDRESSBOOK_DISPLAY_NAME = "Contacts"
ADDRESSBOOK_DESCRIPTION = "Personal address book"
MAX_RESOURCE_SIZE = 10 * 1024 * 1024
VCARD_CONTENT_TYPE = "text/vcard; charset=utf-8"

_PRIVILEGES = (
    "read",
    "write",
    "write-properties",
    "write-content",
    "bind",
    "unbind",
    "read-current-user-privilege-set",
)


@attr.s(auto_attribs=True, frozen=True)
class PropertyContext:
    token: str
    owner_username: str


@attr.s(auto_attribs=True, frozen=True)
class PropertyTable:
    """The properties one resource can report, keyed by name.

    ``allprop_names`` is the subset returned for a ``DAV:allprop`` request: the cheap live properties only, so that
    a bare allprop over the whole address book does not serialise every vCard.
    """

    builders: Mapping[PropName, Callable[[], ElementTree.Element]]
    allprop_names: tuple[PropName, ...]

    def get(self, name: PropName) -> ElementTree.Element | None:
        builder = self.builders.get(name)
        return builder() if builder is not None else None

    @property
    def names(self) -> tuple[PropName, ...]:
        return tuple(self.builders)


def _name(namespace: str, tag: str) -> PropName:
    return PropName(namespace=namespace, tag=tag)


def _resourcetype(*children: ElementTree.Element) -> ElementTree.Element:
    return with_children(dav("resourcetype"), children)


def _privilege_set() -> ElementTree.Element:
    node = dav("current-user-privilege-set")
    for privilege in _PRIVILEGES:
        node.append(with_children(dav("privilege"), (dav(privilege),)))
    return node


def _supported_report_set(*report_tags: tuple[str, str]) -> ElementTree.Element:
    node = dav("supported-report-set")
    for namespace, tag in report_tags:
        report = dav("report")
        report.append(element(namespace, tag))
        node.append(with_children(dav("supported-report"), (report,)))
    return node


def _supported_address_data() -> ElementTree.Element:
    node = carddav("supported-address-data")
    for version in ("3.0", "4.0"):
        entry = carddav("address-data-type")
        entry.set("content-type", "text/vcard")
        entry.set("version", version)
        node.append(entry)
    return node


def _http_date(iso_timestamp: str) -> str:
    return format_datetime(datetime.fromisoformat(iso_timestamp), usegmt=True)


def _common_builders(display_name: str) -> dict[PropName, Callable[[], ElementTree.Element]]:
    return {
        _name(DAV_NS, "displayname"): lambda: dav("displayname", display_name),
        _name(DAV_NS, "current-user-principal"): lambda: with_children(
            dav("current-user-principal"), (href_element(PRINCIPAL_PATH),)
        ),
        _name(DAV_NS, "owner"): lambda: with_children(dav("owner"), (href_element(PRINCIPAL_PATH),)),
        _name(DAV_NS, "current-user-privilege-set"): _privilege_set,
    }


def _collection_table(
    display_name: str,
    resourcetype: Callable[[], ElementTree.Element],
    extra: dict[PropName, Callable[[], ElementTree.Element]],
) -> PropertyTable:
    builders = _common_builders(display_name)
    builders[_name(DAV_NS, "resourcetype")] = resourcetype
    builders.update(extra)
    cheap = tuple(name for name in builders if name != _name(CARDDAV_NS, "address-data"))
    return PropertyTable(builders=builders, allprop_names=cheap)


def property_table(resource: Resource, contact: Contact | None, context: PropertyContext) -> PropertyTable:
    if resource.kind is ResourceKind.ROOT:
        return _collection_table("contacts", lambda: _resourcetype(dav("collection")), {})

    if resource.kind is ResourceKind.PRINCIPAL:
        return _collection_table(
            context.owner_username,
            lambda: _resourcetype(dav("collection"), dav("principal")),
            {
                _name(DAV_NS, "principal-URL"): lambda: with_children(
                    dav("principal-URL"), (href_element(PRINCIPAL_PATH),)
                ),
                _name(CARDDAV_NS, "addressbook-home-set"): lambda: with_children(
                    carddav("addressbook-home-set"), (href_element(HOME_PATH),)
                ),
                _name(DAV_NS, "supported-report-set"): lambda: _supported_report_set(),
            },
        )

    if resource.kind is ResourceKind.PRINCIPALS:
        return _collection_table("Principals", lambda: _resourcetype(dav("collection")), {})

    if resource.kind is ResourceKind.ADDRESSBOOKS:
        return _collection_table("Address Books", lambda: _resourcetype(dav("collection")), {})

    if resource.kind is ResourceKind.HOME:
        return _collection_table(context.owner_username, lambda: _resourcetype(dav("collection")), {})

    if resource.kind is ResourceKind.ADDRESSBOOK:
        return _collection_table(
            ADDRESSBOOK_DISPLAY_NAME,
            lambda: _resourcetype(dav("collection"), carddav("addressbook")),
            {
                _name(CARDDAV_NS, "addressbook-description"): lambda: carddav(
                    "addressbook-description", ADDRESSBOOK_DESCRIPTION
                ),
                _name(CARDDAV_NS, "supported-address-data"): _supported_address_data,
                _name(CARDDAV_NS, "max-resource-size"): lambda: carddav("max-resource-size", str(MAX_RESOURCE_SIZE)),
                _name(CALSERVER_NS, "getctag"): lambda: element(CALSERVER_NS, "getctag", context.token),
                _name(DAV_NS, "sync-token"): lambda: dav("sync-token", sync_token(context.token)),
                _name(DAV_NS, "supported-report-set"): lambda: _supported_report_set(
                    (CARDDAV_NS, "addressbook-multiget"),
                    (CARDDAV_NS, "addressbook-query"),
                    (DAV_NS, "sync-collection"),
                ),
            },
        )

    assert contact is not None, "a contact resource must be resolved before its properties are read"
    builders = _common_builders(contact.display_name)
    builders.update(
        {
            _name(DAV_NS, "resourcetype"): lambda: dav("resourcetype"),
            _name(DAV_NS, "getetag"): lambda: dav("getetag", contact.etag),
            _name(DAV_NS, "getcontenttype"): lambda: dav("getcontenttype", VCARD_CONTENT_TYPE),
            _name(DAV_NS, "getcontentlength"): lambda: dav(
                "getcontentlength", str(len(contact.vcard.encode("utf-8")))
            ),
            _name(DAV_NS, "getlastmodified"): lambda: dav("getlastmodified", _http_date(contact.updated_at)),
            _name(CARDDAV_NS, "address-data"): lambda: carddav("address-data", contact.vcard),
        }
    )
    cheap = tuple(name for name in builders if name != _name(CARDDAV_NS, "address-data"))
    return PropertyTable(builders=builders, allprop_names=cheap)
