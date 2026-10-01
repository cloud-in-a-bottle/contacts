from collections.abc import Sequence
from enum import Enum
from xml.etree import ElementTree

import attr

from server.dav.paths import Resource
from server.dav.props import PropertyContext
from server.dav.props import property_table
from server.dav.xml import DAV_NS
from server.dav.xml import STATUS_NOT_FOUND
from server.dav.xml import STATUS_OK
from server.dav.xml import PropName
from server.dav.xml import PropStatus
from server.dav.xml import ResponseEntry
from server.dav.xml import parse_request_body
from server.models import Contact


class PropfindMode(Enum):
    PROP = "prop"
    ALLPROP = "allprop"
    PROPNAME = "propname"


@attr.s(auto_attribs=True, frozen=True)
class PropfindQuery:
    mode: PropfindMode
    names: tuple[PropName, ...] = ()


@attr.s(auto_attribs=True, frozen=True)
class Target:
    resource: Resource
    contact: Contact | None = None


def parse_propfind(body: bytes) -> PropfindQuery:
    """Parse a PROPFIND body.  An empty body means ``allprop``, per RFC 4918."""
    root = parse_request_body(body)
    if root is None:
        return PropfindQuery(mode=PropfindMode.ALLPROP)
    if root.find(f"{{{DAV_NS}}}propname") is not None:
        return PropfindQuery(mode=PropfindMode.PROPNAME)
    prop_node = root.find(f"{{{DAV_NS}}}prop")
    if prop_node is None:
        return PropfindQuery(mode=PropfindMode.ALLPROP)
    return PropfindQuery(
        mode=PropfindMode.PROP,
        names=tuple(PropName.from_qualified(child.tag) for child in prop_node),
    )


def response_entry(target: Target, query: PropfindQuery, context: PropertyContext) -> ResponseEntry:
    table = property_table(target.resource, target.contact, context)

    if query.mode is PropfindMode.PROPNAME:
        empty = tuple(ElementTree.Element(name.qualified) for name in table.names)
        return ResponseEntry(href=target.resource.path, propstats=(PropStatus(status=STATUS_OK, properties=empty),))

    if query.mode is PropfindMode.ALLPROP:
        found = tuple(value for name in table.allprop_names if (value := table.get(name)) is not None)
        return ResponseEntry(href=target.resource.path, propstats=(PropStatus(status=STATUS_OK, properties=found),))

    found_properties: list[ElementTree.Element] = []
    missing_properties: list[ElementTree.Element] = []
    for name in query.names:
        value = table.get(name)
        if value is None:
            missing_properties.append(ElementTree.Element(name.qualified))
        else:
            found_properties.append(value)

    propstats: list[PropStatus] = []
    if found_properties:
        propstats.append(PropStatus(status=STATUS_OK, properties=tuple(found_properties)))
    if missing_properties:
        propstats.append(PropStatus(status=STATUS_NOT_FOUND, properties=tuple(missing_properties)))
    if not propstats:
        propstats.append(PropStatus(status=STATUS_OK, properties=()))
    return ResponseEntry(href=target.resource.path, propstats=tuple(propstats))


def response_entries(
    targets: Sequence[Target], query: PropfindQuery, context: PropertyContext
) -> tuple[ResponseEntry, ...]:
    return tuple(response_entry(target, query, context) for target in targets)
