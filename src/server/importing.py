import attr

from server.store import ContactStore
from server.vcard.build import new_uid
from server.vcard.split import UTF8_BOM
from server.vcard.split import split_cards
from server.vcard.summary import summarize

# A generous ceiling for one uploaded file.  Exports with embedded photos are the large case; a few thousand
# contacts with photos still lands well under this.
MAX_IMPORT_BYTES = 32 * 1024 * 1024


class VcfImportError(Exception):
    """The uploaded file could not be read as vCards at all."""


@attr.s(auto_attribs=True, frozen=True)
class ImportedCard:
    resource_name: str
    display_name: str


@attr.s(auto_attribs=True, frozen=True)
class SkippedCard:
    display_name: str
    reason: str


@attr.s(auto_attribs=True, frozen=True)
class ImportReport:
    imported: tuple[ImportedCard, ...]
    skipped: tuple[SkippedCard, ...]

    @property
    def total(self) -> int:
        return len(self.imported) + len(self.skipped)


def decode_upload(raw: bytes) -> str:
    """Decode an uploaded .vcf as UTF-8, which is what every current exporter writes."""
    if len(raw) > MAX_IMPORT_BYTES:
        raise VcfImportError(
            f"that file is {len(raw) // (1024 * 1024)} MB; the limit is {MAX_IMPORT_BYTES // (1024 * 1024)} MB"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise VcfImportError(
            "that file is not valid UTF-8. Re-export it as vCard (UTF-8) — an ANSI or UTF-16 export will not read."
        ) from error
    return text.lstrip(UTF8_BOM)


def import_vcf(store: ContactStore, raw: bytes) -> ImportReport:
    """Add every vCard in an uploaded file that is not already stored.

    Duplicates are decided two ways: by UID, which most exporters emit and which identifies the same contact
    across re-exports, and failing that by the exact bytes of the card, which catches re-importing the very same
    file from an exporter (Google Contacts among them) that writes no UID at all.  Both checks also run against
    the cards seen earlier in the same file, so a file containing a contact twice imports it once.

    Nothing is ever overwritten: an import only adds.
    """
    text = decode_upload(raw)
    cards = split_cards(text)
    if not cards:
        raise VcfImportError(
            "no vCards found in that file. It should contain BEGIN:VCARD … END:VCARD blocks — a Google Contacts "
            "CSV export will not work, choose the vCard format instead."
        )

    fingerprints = store.fingerprints()
    seen_uids = set(fingerprints.uids)
    seen_etags = set(fingerprints.etags)

    to_write: list[tuple[str, str]] = []
    imported: list[ImportedCard] = []
    skipped: list[SkippedCard] = []

    for card in cards:
        resource_name = new_uid()
        summary = summarize(card, fallback_uid=resource_name)
        display_name = summary.display_name if summary.display_name != resource_name else "(unnamed)"

        if summary.etag in seen_etags:
            skipped.append(SkippedCard(display_name=display_name, reason="an identical card is already stored"))
            continue
        # summarize() falls back to the resource name when the card has no UID, and that fresh uuid can never
        # collide — so this only matches when the card carried a real UID.
        if summary.uid in seen_uids:
            skipped.append(SkippedCard(display_name=display_name, reason="already stored under the same UID"))
            continue

        seen_uids.add(summary.uid)
        seen_etags.add(summary.etag)
        to_write.append((resource_name, card))
        imported.append(ImportedCard(resource_name=resource_name, display_name=display_name))

    store.put_many(to_write)
    return ImportReport(imported=tuple(imported), skipped=tuple(skipped))
