import attr

from server.vcard.model import ContactFields
from server.vcard.model import StructuredName
from server.vcard.parse import build_search_text
from server.vcard.parse import extract_fields
from server.vcard.parse import extract_uid
from server.vcard.parse import has_inline_photo
from server.vcard.parse import parse_vcard


@attr.s(auto_attribs=True, frozen=True)
class VCardSummary:
    """Everything we derive from a vCard's text in order to index and list it.

    The text itself stays the source of truth: a card a CardDAV client wrote is stored byte-for-byte, so properties
    this app knows nothing about survive a round trip.  The card's ETag is not here — that is the identity of its
    bytes, which the repository defines; see :func:`server.store.content_etag`.
    """

    uid: str
    display_name: str
    sort_key: str
    search_text: str
    subtitle: str
    initials: str
    has_photo: bool
    fields: ContactFields


def summarize(text: str, fallback_uid: str) -> VCardSummary:
    card = parse_vcard(text)
    fields = extract_fields(card)
    display_name = fields.best_display_name or fallback_uid
    return VCardSummary(
        uid=extract_uid(card) or fallback_uid,
        display_name=display_name,
        sort_key=_sort_key(fields, display_name),
        search_text=build_search_text(card, fields),
        subtitle=_subtitle(fields),
        initials=initials_for(display_name, fields.name),
        has_photo=has_inline_photo(card),
        fields=fields,
    )


def _subtitle(fields: ContactFields) -> str:
    """The second line of a contact in the list: whatever identifies them after their name."""
    for candidate in (
        fields.organization,
        fields.emails[0].value if fields.emails else "",
        fields.phones[0].value if fields.phones else "",
    ):
        if candidate:
            return candidate
    return ""


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


def _sort_key(fields: ContactFields, display_name: str) -> str:
    """Sort by family name when we have one, so the list reads like an address book rather than a list of firsts."""
    if fields.name.family:
        return f"{fields.name.family} {fields.name.given}".strip().lower()
    return display_name.lower()
