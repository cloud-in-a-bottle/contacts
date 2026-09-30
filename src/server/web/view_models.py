import attr

from server.models import Contact
from server.vcard.model import ContactFields
from server.vcard.model import StructuredName


@attr.s(auto_attribs=True, frozen=True)
class ListEntry:
    resource_name: str
    display_name: str
    subtitle: str
    initials: str
    has_photo: bool


def initials_for(display_name: str, name: StructuredName | None = None) -> str:
    """Two letters for the avatar.

    The structured name is used when there is one, because the display name often leads with an honorific and
    "Dr. Amara Okonkwo" should read AO, not DO.
    """
    if name is not None and (name.given or name.family):
        return ((name.given[:1] or name.family[:1]) + (name.family[:1] if name.given else "")).upper()
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
        initials=initials_for(contact.display_name, fields.name),
        has_photo=has_photo,
    )
