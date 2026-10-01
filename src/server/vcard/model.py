import attr

from server.vcard.lines import ContentLine


@attr.s(auto_attribs=True, frozen=True)
class VCard:
    lines: tuple[ContentLine, ...]

    def first(self, name: str) -> ContentLine | None:
        wanted = name.upper()
        for line in self.lines:
            if line.name == wanted:
                return line
        return None

    def all(self, name: str) -> tuple[ContentLine, ...]:
        wanted = name.upper()
        return tuple(line for line in self.lines if line.name == wanted)


@attr.s(auto_attribs=True, frozen=True)
class TypedValue:
    value: str
    label: str = ""


@attr.s(auto_attribs=True, frozen=True)
class PostalAddress:
    street: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str = ""
    label: str = ""

    @property
    def is_empty(self) -> bool:
        return not any((self.street, self.city, self.region, self.postal_code, self.country))

    @property
    def one_line(self) -> str:
        locality = " ".join(part for part in (self.city, self.region, self.postal_code) if part)
        return ", ".join(part for part in (self.street, locality, self.country) if part)


@attr.s(auto_attribs=True, frozen=True)
class StructuredName:
    family: str = ""
    given: str = ""
    additional: str = ""
    prefix: str = ""
    suffix: str = ""

    @property
    def is_empty(self) -> bool:
        return not any((self.family, self.given, self.additional, self.prefix, self.suffix))

    @property
    def display(self) -> str:
        return " ".join(part for part in (self.prefix, self.given, self.additional, self.family, self.suffix) if part)


@attr.s(auto_attribs=True, frozen=True)
class ContactFields:
    """The parts of a contact the web UI understands and can rewrite."""

    formatted_name: str = ""
    name: StructuredName = StructuredName()
    nickname: str = ""
    organization: str = ""
    department: str = ""
    job_title: str = ""
    emails: tuple[TypedValue, ...] = ()
    phones: tuple[TypedValue, ...] = ()
    addresses: tuple[PostalAddress, ...] = ()
    urls: tuple[TypedValue, ...] = ()
    birthday: str = ""
    note: str = ""
    categories: tuple[str, ...] = ()

    @property
    def best_display_name(self) -> str:
        for candidate in (self.formatted_name, self.name.display, self.nickname, self.organization):
            if candidate.strip():
                return candidate.strip()
        for typed in (*self.emails, *self.phones):
            if typed.value.strip():
                return typed.value.strip()
        return ""


@attr.s(auto_attribs=True, frozen=True)
class Photo:
    data: bytes
    media_type: str
