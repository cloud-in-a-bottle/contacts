import uuid
from datetime import UTC
from datetime import datetime

from server.vcard.lines import ContentLine
from server.vcard.lines import Param
from server.vcard.lines import escape_text
from server.vcard.lines import fold
from server.vcard.lines import render_line
from server.vcard.model import ContactFields
from server.vcard.model import VCard
from server.vcard.parse import MANAGED_PROPERTIES
from server.vcard.parse import STRUCTURAL_PROPERTIES
from server.vcard.parse import parse_vcard

PRODUCT_ID = "-//cloud-in-a-bottle//contacts//EN"
VCARD_VERSION = "3.0"

_LABEL_TO_TYPE = {
    "mobile": "CELL",
    "cell": "CELL",
    "iphone": "IPHONE",
    "home": "HOME",
    "work": "WORK",
    "fax": "FAX",
    "pager": "PAGER",
    "main": "MAIN",
    "other": "OTHER",
    "voice": "VOICE",
}


def new_uid() -> str:
    return str(uuid.uuid4())


def _type_param(label: str) -> tuple[Param, ...]:
    kinds = [
        _LABEL_TO_TYPE.get(part.strip().lower(), part.strip().upper())
        for part in label.replace(",", "/").split("/")
        if part.strip()
    ]
    if not kinds:
        return ()
    return (Param(name="TYPE", values=tuple(dict.fromkeys(kinds))),)


def _line(name: str, value: str, params: tuple[Param, ...] = ()) -> ContentLine:
    return ContentLine(name=name, value=value, params=params)


def _structured(parts: list[str]) -> str:
    return ";".join(escape_text(part) for part in parts)


def _managed_lines(fields: ContactFields, uid: str) -> list[ContentLine]:
    lines = [_line("UID", escape_text(uid)), _line("FN", escape_text(fields.best_display_name))]

    name = fields.name
    if name.is_empty and fields.formatted_name:
        # Keep N present (Apple Contacts and Thunderbird both expect it) by guessing from the display name.
        given, _, family = fields.formatted_name.partition(" ")
        name = name.__class__(family=family.strip(), given=given.strip())
    if not name.is_empty:
        lines.append(_line("N", _structured([name.family, name.given, name.additional, name.prefix, name.suffix])))

    if fields.nickname:
        lines.append(_line("NICKNAME", escape_text(fields.nickname)))
    if fields.organization or fields.department:
        lines.append(_line("ORG", _structured([fields.organization, fields.department])))
    if fields.job_title:
        lines.append(_line("TITLE", escape_text(fields.job_title)))

    for email in fields.emails:
        if email.value.strip():
            lines.append(_line("EMAIL", escape_text(email.value.strip()), _type_param(email.label)))
    for phone in fields.phones:
        if phone.value.strip():
            lines.append(_line("TEL", escape_text(phone.value.strip()), _type_param(phone.label)))
    for address in fields.addresses:
        if not address.is_empty:
            lines.append(
                _line(
                    "ADR",
                    _structured(
                        ["", "", address.street, address.city, address.region, address.postal_code, address.country]
                    ),
                    _type_param(address.label),
                )
            )
    for url in fields.urls:
        if url.value.strip():
            lines.append(_line("URL", escape_text(url.value.strip()), _type_param(url.label)))

    if fields.birthday:
        lines.append(_line("BDAY", escape_text(fields.birthday)))
    if fields.note:
        lines.append(_line("NOTE", escape_text(fields.note)))
    if fields.categories:
        lines.append(_line("CATEGORIES", ",".join(escape_text(category) for category in fields.categories)))
    return lines


def _preserved_lines(existing: VCard) -> list[ContentLine]:
    """Lines the editor does not own — PHOTO, X-* extensions, IMPP, and anything else a client put there."""
    return [line for line in existing.lines if line.name not in MANAGED_PROPERTIES | STRUCTURAL_PROPERTIES]


def render_vcard(fields: ContactFields, uid: str, existing_text: str | None = None) -> str:
    """Render a vCard for ``fields``, carrying over any properties from ``existing_text`` that the editor cannot edit.

    Written as vCard 3.0, which every CardDAV client in common use reads.
    """
    existing = parse_vcard(existing_text) if existing_text else VCard(lines=())
    revision = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    all_lines = [
        _line("BEGIN", "VCARD"),
        _line("VERSION", VCARD_VERSION),
        _line("PRODID", PRODUCT_ID),
        *_managed_lines(fields, uid),
        *_preserved_lines(existing),
        _line("REV", revision),
        _line("END", "VCARD"),
    ]
    output: list[str] = []
    for line in all_lines:
        output.extend(fold(render_line(line)))
    return "\r\n".join(output) + "\r\n"
