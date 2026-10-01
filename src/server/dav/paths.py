import re
from enum import Enum
from urllib.parse import quote
from urllib.parse import unquote

import attr

from server.naming import is_safe_resource_name

# The CardDAV URL space.  These are absolute paths on the app's own origin, so they can be handed straight to a
# client as hrefs.  ``/dav`` is the prefix declared in openhost.toml's public_paths.
DAV_ROOT = "/dav"
PRINCIPALS_PATH = f"{DAV_ROOT}/principals/"
PRINCIPAL_PATH = f"{PRINCIPALS_PATH}owner/"
ADDRESSBOOKS_PATH = f"{DAV_ROOT}/addressbooks/"
HOME_PATH = f"{ADDRESSBOOKS_PATH}owner/"
ADDRESSBOOK_PATH = f"{HOME_PATH}default/"
WELL_KNOWN_PATH = "/.well-known/carddav"

VCARD_SUFFIX = ".vcf"


class ResourceKind(Enum):
    ROOT = "root"
    PRINCIPALS = "principals"
    PRINCIPAL = "principal"
    ADDRESSBOOKS = "addressbooks"
    HOME = "home"
    ADDRESSBOOK = "addressbook"
    CONTACT = "contact"


@attr.s(auto_attribs=True, frozen=True)
class Resource:
    kind: ResourceKind
    path: str
    resource_name: str = ""

    @property
    def is_collection(self) -> bool:
        return self.kind is not ResourceKind.CONTACT


def contact_path(resource_name: str) -> str:
    return ADDRESSBOOK_PATH + quote(resource_name + VCARD_SUFFIX, safe="")


def resolve(raw_path: str) -> Resource | None:
    """Map a request path onto a resource, or None if nothing lives there.

    Collections are accepted with or without a trailing slash; the resolved ``path`` always carries one so that the
    hrefs we hand back are stable.
    """
    path = unquote(raw_path)
    path = re.sub(r"/{2,}", "/", path)
    if not path.startswith(DAV_ROOT):
        return None
    with_slash = path if path.endswith("/") else path + "/"

    if with_slash == f"{DAV_ROOT}/":
        return Resource(kind=ResourceKind.ROOT, path=f"{DAV_ROOT}/")
    if with_slash == PRINCIPALS_PATH:
        return Resource(kind=ResourceKind.PRINCIPALS, path=PRINCIPALS_PATH)
    if with_slash == ADDRESSBOOKS_PATH:
        return Resource(kind=ResourceKind.ADDRESSBOOKS, path=ADDRESSBOOKS_PATH)
    if with_slash == PRINCIPAL_PATH:
        return Resource(kind=ResourceKind.PRINCIPAL, path=PRINCIPAL_PATH)
    if with_slash == HOME_PATH:
        return Resource(kind=ResourceKind.HOME, path=HOME_PATH)
    if with_slash == ADDRESSBOOK_PATH:
        return Resource(kind=ResourceKind.ADDRESSBOOK, path=ADDRESSBOOK_PATH)

    if path.startswith(ADDRESSBOOK_PATH):
        remainder = path[len(ADDRESSBOOK_PATH) :]
        resource_name = parse_resource_name(remainder)
        if resource_name is not None:
            return Resource(kind=ResourceKind.CONTACT, path=contact_path(resource_name), resource_name=resource_name)
    return None


def parse_resource_name(segment: str) -> str | None:
    """Turn a ``<name>.vcf`` path segment into the resource name it addresses."""
    if "/" in segment or not segment.endswith(VCARD_SUFFIX):
        return None
    resource_name = segment[: -len(VCARD_SUFFIX)]
    if not is_safe_resource_name(resource_name):
        return None
    return resource_name


def resource_name_from_href(href: str) -> str | None:
    """Extract the resource name from an href sent by a client, which may be absolute or relative."""
    path = unquote(href.split("?", 1)[0])
    path = re.sub(r"/{2,}", "/", path)
    if "://" in path:
        path = "/" + path.split("://", 1)[1].partition("/")[2]
    if not path.startswith(ADDRESSBOOK_PATH):
        return None
    return parse_resource_name(path[len(ADDRESSBOOK_PATH) :])


# The static part of the collection tree, used to answer a PROPFIND with Depth greater than 0.
_STATIC_CHILDREN = {
    ResourceKind.ROOT: (
        Resource(kind=ResourceKind.PRINCIPALS, path=PRINCIPALS_PATH),
        Resource(kind=ResourceKind.ADDRESSBOOKS, path=ADDRESSBOOKS_PATH),
    ),
    ResourceKind.PRINCIPALS: (Resource(kind=ResourceKind.PRINCIPAL, path=PRINCIPAL_PATH),),
    ResourceKind.ADDRESSBOOKS: (Resource(kind=ResourceKind.HOME, path=HOME_PATH),),
    ResourceKind.HOME: (Resource(kind=ResourceKind.ADDRESSBOOK, path=ADDRESSBOOK_PATH),),
}


def static_children(resource: Resource) -> tuple[Resource, ...]:
    """The collections nested directly under ``resource``.  Contacts are not included; they come from the store."""
    return _STATIC_CHILDREN.get(resource.kind, ())
