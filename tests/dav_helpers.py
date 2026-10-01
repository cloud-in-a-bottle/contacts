from xml.etree import ElementTree

import attr

DAV = "DAV:"
CARDDAV = "urn:ietf:params:xml:ns:carddav"
CALSERVER = "http://calendarserver.org/ns/"

VCARD = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "UID:{uid}\r\n"
    "FN:{name}\r\n"
    "N:{last};{first};;;\r\n"
    "EMAIL;TYPE=WORK:{email}\r\n"
    "END:VCARD\r\n"
)


def vcard(uid: str, name: str, email: str = "someone@example.test") -> str:
    first, _, last = name.partition(" ")
    return VCARD.format(uid=uid, name=name, first=first, last=last or first, email=email)


@attr.s(auto_attribs=True, frozen=True)
class Entry:
    href: str
    status: str | None
    properties: dict[str, ElementTree.Element]
    missing: tuple[str, ...]

    def text(self, qualified: str) -> str:
        return (self.properties[qualified].text or "").strip()


@attr.s(auto_attribs=True, frozen=True)
class Multistatus:
    entries: tuple[Entry, ...]
    sync_token: str | None

    @property
    def hrefs(self) -> tuple[str, ...]:
        return tuple(entry.href for entry in self.entries)

    def by_href(self, href: str) -> Entry:
        for entry in self.entries:
            if entry.href == href:
                return entry
        raise AssertionError(f"{href} is not in the multistatus: {self.hrefs}")


def parse_multistatus(body: bytes) -> Multistatus:
    root = ElementTree.fromstring(body)
    assert root.tag == f"{{{DAV}}}multistatus", root.tag
    entries = []
    for response in root.findall(f"{{{DAV}}}response"):
        href_node = response.find(f"{{{DAV}}}href")
        status_node = response.find(f"{{{DAV}}}status")
        properties: dict[str, ElementTree.Element] = {}
        missing: list[str] = []
        for propstat in response.findall(f"{{{DAV}}}propstat"):
            propstat_status = (propstat.findtext(f"{{{DAV}}}status") or "").strip()
            for prop in propstat.findall(f"{{{DAV}}}prop"):
                for child in prop:
                    if " 200 " in propstat_status:
                        properties[child.tag] = child
                    else:
                        missing.append(child.tag)
        entries.append(
            Entry(
                href=(href_node.text or "").strip() if href_node is not None else "",
                status=(status_node.text or "").strip() if status_node is not None else None,
                properties=properties,
                missing=tuple(missing),
            )
        )
    token_node = root.find(f"{{{DAV}}}sync-token")
    return Multistatus(
        entries=tuple(entries),
        sync_token=(token_node.text or "").strip() if token_node is not None else None,
    )


def propfind_body(*properties: tuple[str, str]) -> str:
    requested = "".join(f'<x:{tag} xmlns:x="{namespace}"/>' for namespace, tag in properties)
    return f'<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:prop>{requested}</D:prop></D:propfind>'
