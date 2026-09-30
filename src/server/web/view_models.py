import attr

from server.models import Contact
from server.vcard.model import ContactFields


@attr.s(auto_attribs=True, frozen=True)
class ListEntry:
    resource_name: str
    display_name: str
    subtitle: str
    initials: str
    has_photo: bool


def initials_for(display_name: str) -> str:
    words = [word for word in display_name.split() if word[:1].isalnum()]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][:1] + words[-1][:1]).upper()


def _subtitle(fields: ContactFields) -> str:
    for candidate in (
        fields.organization,
        fields.emails[0].value if fields.emails else "",
        fields.phones[0].value if fields.phones else "",
    ):
        if candidate:
            return candidate
    return ""


def list_entry(contact: Contact, fields: ContactFields, has_photo: bool) -> ListEntry:
    return ListEntry(
        resource_name=contact.resource_name,
        display_name=contact.display_name,
        subtitle=_subtitle(fields),
        initials=initials_for(contact.display_name),
        has_photo=has_photo,
    )
