import base64
import binascii
import re

from server.vcard.lines import ContentLine
from server.vcard.lines import decode_value
from server.vcard.lines import parse_line
from server.vcard.lines import structured_value
from server.vcard.lines import text_value
from server.vcard.lines import unfold
from server.vcard.model import ContactFields
from server.vcard.model import Photo
from server.vcard.model import PostalAddress
from server.vcard.model import StructuredName
from server.vcard.model import TypedValue
from server.vcard.model import VCard

# BEGIN/END/VERSION are structural; the rest are the properties the editor owns and rewrites.
STRUCTURAL_PROPERTIES = frozenset({"BEGIN", "END", "VERSION"})
MANAGED_PROPERTIES = frozenset(
    {
        "FN",
        "N",
        "NICKNAME",
        "ORG",
        "TITLE",
        "EMAIL",
        "TEL",
        "ADR",
        "URL",
        "BDAY",
        "NOTE",
        "CATEGORIES",
        "UID",
        "REV",
        "PRODID",
    }
)

_TYPE_LABELS = {
    "HOME": "home",
    "WORK": "work",
    "CELL": "mobile",
    "MOBILE": "mobile",
    "VOICE": "voice",
    "FAX": "fax",
    "PAGER": "pager",
    "IPHONE": "iPhone",
    "MAIN": "main",
    "OTHER": "other",
    "INTERNET": "",
    "PREF": "",
}


def parse_vcard(text: str) -> VCard:
    lines: list[ContentLine] = []
    for raw_line in unfold(text):
        parsed = parse_line(raw_line)
        if parsed is not None:
            lines.append(parsed)
    return VCard(lines=tuple(lines))


def _label_for(line: ContentLine) -> str:
    labels = [_TYPE_LABELS.get(kind, kind.lower().removeprefix("x-")) for kind in line.types]
    return "/".join(label for label in labels if label)


def _typed_values(card: VCard, name: str) -> tuple[TypedValue, ...]:
    values: list[TypedValue] = []
    for line in card.all(name):
        value = text_value(line).strip()
        if value:
            values.append(TypedValue(value=value, label=_label_for(line)))
    return tuple(values)


def _field(components: tuple[tuple[str, ...], ...], index: int) -> str:
    if index >= len(components):
        return ""
    return ", ".join(part for part in components[index] if part).strip()


def extract_fields(card: VCard) -> ContactFields:
    name = StructuredName()
    if (name_line := card.first("N")) is not None:
        components = structured_value(name_line)
        name = StructuredName(
            family=_field(components, 0),
            given=_field(components, 1),
            additional=_field(components, 2),
            prefix=_field(components, 3),
            suffix=_field(components, 4),
        )

    organization = ""
    department = ""
    if (org_line := card.first("ORG")) is not None:
        components = structured_value(org_line)
        organization = _field(components, 0)
        department = "; ".join(_field(components, index) for index in range(1, len(components))).strip("; ")

    addresses: list[PostalAddress] = []
    for address_line in card.all("ADR"):
        components = structured_value(address_line)
        # ADR is post-office-box;extended;street;locality;region;postal-code;country.  The first two are deprecated
        # and rarely carry anything worth showing separately, so they are folded into the street line.
        street = "; ".join(
            part for part in (_field(components, 0), _field(components, 1), _field(components, 2)) if part
        )
        address = PostalAddress(
            street=street,
            city=_field(components, 3),
            region=_field(components, 4),
            postal_code=_field(components, 5),
            country=_field(components, 6),
            label=_label_for(address_line),
        )
        if not address.is_empty:
            addresses.append(address)

    categories: list[str] = []
    for categories_line in card.all("CATEGORIES"):
        components = structured_value(categories_line)
        for field in components:
            categories.extend(part.strip() for part in field if part.strip())

    formatted_name_line = card.first("FN")
    nickname_line = card.first("NICKNAME")
    title_line = card.first("TITLE")
    birthday_line = card.first("BDAY")
    note_line = card.first("NOTE")

    return ContactFields(
        formatted_name=text_value(formatted_name_line).strip() if formatted_name_line else "",
        name=name,
        nickname=text_value(nickname_line).strip() if nickname_line else "",
        organization=organization,
        department=department,
        job_title=text_value(title_line).strip() if title_line else "",
        emails=_typed_values(card, "EMAIL"),
        phones=_typed_values(card, "TEL"),
        addresses=tuple(addresses),
        urls=_typed_values(card, "URL"),
        birthday=text_value(birthday_line).strip() if birthday_line else "",
        note=text_value(note_line).strip() if note_line else "",
        categories=tuple(categories),
    )


def extract_uid(card: VCard) -> str:
    uid_line = card.first("UID")
    if uid_line is None:
        return ""
    return text_value(uid_line).strip()


_DATA_URI = re.compile(r"^data:([\w.+/-]+)?(;base64)?,", re.IGNORECASE)

# The photo route serves these bytes on the admin UI's origin with this media type, and the type comes from whoever
# wrote the card — a CardDAV client included.  Anything a browser would run as a document (text/html,
# image/svg+xml, ...) would be script on the owner's session, so only raster formats are ever served.
_SERVABLE_PHOTO_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_PHOTO_TYPE_ALIASES = {"image/jpg": "image/jpeg"}


def extract_photo(card: VCard) -> Photo | None:
    """Decode an inline PHOTO, covering the vCard 3.0 (``ENCODING=B``) and 4.0 (``data:`` URI) spellings.

    A PHOTO that is a plain URL is left for the browser to fetch itself, so it is reported as absent here.
    """
    photo_line = card.first("PHOTO")
    if photo_line is None:
        return None
    # A 4.0 data: URI is a URI value, so it is normally unescaped — but some producers escape the comma anyway.
    raw = decode_value(photo_line).strip().replace("\\,", ",")
    media_type = ""
    declared_type = photo_line.param("TYPE")
    if declared_type and declared_type[0].upper() not in ("URI", "URL"):
        subtype = declared_type[0].lower()
        media_type = subtype if "/" in subtype else f"image/{subtype}"

    if (match := _DATA_URI.match(raw)) is not None:
        if not match.group(2):
            return None
        media_type = match.group(1) or media_type
        raw = raw[match.end() :]
    elif not any(value.upper() in ("B", "BASE64") for value in photo_line.param("ENCODING")):
        return None

    try:
        data = base64.b64decode("".join(raw.split()), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not data:
        return None
    media_type = media_type.lower() or "image/jpeg"
    media_type = _PHOTO_TYPE_ALIASES.get(media_type, media_type)
    if media_type not in _SERVABLE_PHOTO_TYPES:
        return None
    return Photo(data=data, media_type=media_type)


def build_search_text(card: VCard, fields: ContactFields) -> str:
    """A lowercased haystack for the admin UI's search box."""
    parts: list[str] = [
        fields.formatted_name,
        fields.name.display,
        fields.nickname,
        fields.organization,
        fields.department,
        fields.job_title,
        fields.note,
        *(typed.value for typed in fields.emails),
        *(typed.value for typed in fields.phones),
        *(typed.value for typed in fields.urls),
        *(address.one_line for address in fields.addresses),
        *fields.categories,
    ]
    # Digits-only forms of phone numbers so that searching "5551234" finds "+1 (555) 123-4".
    parts.extend(re.sub(r"\D", "", typed.value) for typed in fields.phones)
    return " ".join(part.lower() for part in parts if part).strip()


def has_inline_photo(card: VCard) -> bool:
    """Whether this card carries a photo we could serve, without paying to decode it.

    The list page needs this for every contact, and an inline PHOTO is the largest thing in a vCard — base64
    decoding each one just to answer yes or no is what made listing an address book slow.  This checks the
    shape instead; a PHOTO whose base64 turns out to be corrupt will 404 when actually fetched.
    """
    photo_line = card.first("PHOTO")
    if photo_line is None:
        return False
    if any(value.upper() in ("B", "BASE64") for value in photo_line.param("ENCODING")):
        return True
    return bool(_DATA_URI.match(decode_value(photo_line).strip().replace("\\,", ",")))
