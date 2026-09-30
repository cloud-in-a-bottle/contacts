import pytest
from dav_helpers import vcard
from litestar import Litestar
from litestar.testing import TestClient

from server.exporting import build_export
from server.exporting import export_filename
from server.importing import VcfImportError
from server.importing import import_vcf
from server.store import ContactStore
from server.vcard.split import split_cards

# What contacts.google.com actually writes for "vCard (for iOS Contacts)": vCard 3.0, CRLF, no UID property,
# an empty trailing line between cards, and a folded PHOTO.
GOOGLE_EXPORT = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "FN:Amara Okonkwo\r\n"
    "N:Okonkwo;Amara;;;\r\n"
    "EMAIL;TYPE=INTERNET;TYPE=HOME:amara@example.test\r\n"
    "TEL;TYPE=CELL:+44 7700 900123\r\n"
    "CATEGORIES:myContacts\r\n"
    "END:VCARD\r\n"
    "\r\n"
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "FN:Ines Bergström\r\n"
    "N:Bergström;Ines;;;\r\n"
    "ORG:Nordlys AS\r\n"
    "EMAIL;TYPE=INTERNET;TYPE=WORK:ines@nordlys.example\r\n"
    "NOTE:Met at the Oslo meetup\\, spring 2025.\r\n"
    "END:VCARD\r\n"
)


def test_split_handles_the_shapes_exporters_actually_write() -> None:
    assert len(split_cards(GOOGLE_EXPORT)) == 2
    assert len(split_cards(GOOGLE_EXPORT.replace("\r\n", "\n"))) == 2
    assert len(split_cards("﻿" + GOOGLE_EXPORT)) == 2
    assert split_cards("") == ()
    assert split_cards("Name,Email\r\nAmara,amara@example.test\r\n") == ()


def test_split_slices_cards_out_unchanged() -> None:
    first, second = split_cards(GOOGLE_EXPORT)
    assert first.startswith("BEGIN:VCARD\r\n") and first.endswith("END:VCARD\r\n")
    assert "Amara Okonkwo" in first
    assert "Ines Bergström" in second
    assert "Amara" not in second


def test_importing_a_google_export(store: ContactStore) -> None:
    report = import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))

    assert [entry.display_name for entry in report.imported] == ["Amara Okonkwo", "Ines Bergström"]
    assert report.skipped == ()
    assert store.count() == 2

    stored = {contact.display_name: contact for contact in store.list_contacts()}
    # Stored byte-for-byte, so the note's escaping and the TYPE parameters survive.
    assert "NOTE:Met at the Oslo meetup\\, spring 2025." in stored["Ines Bergström"].vcard
    assert "TEL;TYPE=CELL:+44 7700 900123" in stored["Amara Okonkwo"].vcard


def test_reimporting_the_same_file_changes_nothing(store: ContactStore) -> None:
    import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))
    change_seq = store.change_seq()

    second = import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))
    assert second.imported == ()
    assert [entry.reason for entry in second.skipped] == ["an identical card is already stored"] * 2
    assert store.count() == 2
    assert store.change_seq() == change_seq, "a no-op import should not look like a change to syncing clients"


def test_a_card_already_stored_under_the_same_uid_is_skipped(store: ContactStore) -> None:
    store.put("existing", vcard("shared-uid", "Mira Vance"))
    # Same UID, different bytes — the contact was edited since it was exported.
    incoming = vcard("shared-uid", "Mira Vance-Okafor")

    report = import_vcf(store, incoming.encode("utf-8"))
    assert [entry.reason for entry in report.skipped] == ["already stored under the same UID"]
    assert store.count() == 1
    assert store.get("existing").display_name == "Mira Vance"  # type: ignore[union-attr]


def test_a_file_containing_the_same_contact_twice_imports_it_once(store: ContactStore) -> None:
    report = import_vcf(store, (GOOGLE_EXPORT + GOOGLE_EXPORT).encode("utf-8"))
    assert len(report.imported) == 2
    assert len(report.skipped) == 2
    assert store.count() == 2


def test_two_different_contacts_without_uids_both_import(store: ContactStore) -> None:
    report = import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))
    assert len({entry.resource_name for entry in report.imported}) == 2
    assert len({contact.uid for contact in store.list_contacts()}) == 2


def test_an_import_is_one_change_for_syncing_clients(store: ContactStore) -> None:
    baseline = store.change_seq()
    import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))

    delta = store.changes_since(baseline)
    assert len(delta.changed) == 2
    assert len({contact.change_seq for contact in delta.changed}) == 1


def test_unreadable_uploads_are_explained(store: ContactStore) -> None:
    with pytest.raises(VcfImportError, match="not valid UTF-8"):
        import_vcf(store, "BEGIN:VCARD\r\nFN:Ines Bergström\r\nEND:VCARD\r\n".encode("utf-16"))

    with pytest.raises(VcfImportError, match="no vCards found"):
        import_vcf(store, b"Name,Email\r\nAmara,amara@example.test\r\n")

    assert store.count() == 0


def test_an_oversized_upload_is_refused(store: ContactStore) -> None:
    with pytest.raises(VcfImportError, match="the limit is"):
        import_vcf(store, b"x" * (33 * 1024 * 1024))


def test_export_concatenates_the_stored_cards_verbatim(store: ContactStore) -> None:
    import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))
    exported = build_export(store.list_contacts()).decode("utf-8")

    assert len(split_cards(exported)) == 2
    for card in split_cards(GOOGLE_EXPORT):
        assert card in exported


def test_export_round_trips_back_into_an_empty_store(store: ContactStore, database_path: object) -> None:
    import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))
    exported = build_export(store.list_contacts())

    from server.db import Database  # noqa: PLC0415 -- a second store, to import into somewhere empty

    other = ContactStore(Database(database_path.parent / "other.db"))  # type: ignore[attr-defined]
    report = import_vcf(other, exported)

    assert len(report.imported) == 2
    assert {c.vcard for c in other.list_contacts()} == {c.vcard for c in store.list_contacts()}


def test_export_separates_cards_that_lack_a_trailing_newline(store: ContactStore) -> None:
    store.put("a", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:A One\r\nEND:VCARD")
    store.put("b", "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:B Two\r\nEND:VCARD")
    assert len(split_cards(build_export(store.list_contacts()).decode("utf-8"))) == 2


def test_export_of_an_empty_address_book_is_empty(store: ContactStore) -> None:
    assert build_export(store.list_contacts()) == b""


def test_the_filename_carries_the_date() -> None:
    from datetime import UTC  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415

    assert export_filename(datetime(2026, 9, 30, tzinfo=UTC)) == "contacts-2026-09-30.vcf"


def test_uploading_through_the_ui(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    response = owner_client.post(
        "/import", files={"upload": ("google.vcf", GOOGLE_EXPORT.encode("utf-8"), "text/vcard")}
    )
    assert response.status_code == 200
    assert "Imported 2 of 2" in response.text
    assert "Ines Bergström" in response.text
    assert store.count() == 2

    again = owner_client.post("/import", files={"upload": ("google.vcf", GOOGLE_EXPORT.encode("utf-8"), "text/vcard")})
    assert "Imported 0 of 2" in again.text
    assert "already stored" in again.text


def test_a_bad_upload_reports_the_problem_on_the_form(owner_client: TestClient[Litestar]) -> None:
    response = owner_client.post("/import", files={"upload": ("contacts.csv", b"Name,Email\r\n", "text/csv")})
    assert response.status_code == 422
    assert "no vCards found" in response.text


def test_downloading_the_export_through_the_ui(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))
    response = owner_client.get("/export")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/vcard")
    assert response.headers["Content-Disposition"].startswith('attachment; filename="contacts-')
    assert len(split_cards(response.text)) == 2


def test_transfer_pages_are_closed_to_anyone_but_the_owner(anonymous_client: TestClient[Litestar]) -> None:
    assert anonymous_client.get("/import").status_code == 401
    assert anonymous_client.get("/export").status_code == 401
    assert anonymous_client.post("/import", files={"upload": ("a.vcf", b"", "text/vcard")}).status_code == 401


def test_vcard_responses_name_the_charset_exactly_once(
    owner_client: TestClient[Litestar], store: ContactStore
) -> None:
    """Litestar adds "; charset=utf-8" to text/* itself, so the handlers must not also spell it out."""
    import_vcf(store, GOOGLE_EXPORT.encode("utf-8"))
    resource_name = store.list_contacts()[0].resource_name

    for path in ("/export", f"/contacts/{resource_name}/vcard"):
        content_type = owner_client.get(path).headers["Content-Type"]
        assert content_type.count("charset") == 1, f"{path} sent {content_type!r}"
        assert content_type == "text/vcard; charset=utf-8", path
