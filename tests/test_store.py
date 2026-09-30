from server.credentials import CredentialStore
from server.credentials import generate_password
from server.db import Database
from server.store import ContactStore


def card(name: str, email: str = "") -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{name}", f"N:{name.split()[-1]};{name.split()[0]};;;"]
    if email:
        lines.append(f"EMAIL;TYPE=WORK:{email}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def test_put_then_get_round_trips_the_card_verbatim(store: ContactStore) -> None:
    text = card("Mira Vance", "mira@example.test")
    result = store.put("mira", text)
    assert result.was_created is True

    stored = store.get("mira")
    assert stored is not None
    assert stored.vcard == text
    assert stored.display_name == "Mira Vance"
    assert stored.etag == result.contact.etag


def test_put_twice_updates_rather_than_duplicating(store: ContactStore) -> None:
    store.put("mira", card("Mira Vance"))
    created_at = store.get("mira").created_at  # type: ignore[union-attr]
    second = store.put("mira", card("Mira Vance-Okafor"))

    assert second.was_created is False
    assert store.count() == 1
    assert store.get("mira").display_name == "Mira Vance-Okafor"  # type: ignore[union-attr]
    assert store.get("mira").created_at == created_at  # type: ignore[union-attr]


def test_contacts_sort_by_family_name(store: ContactStore) -> None:
    store.put("b", card("Zara Abbott"))
    store.put("a", card("Ana Zielinski"))
    assert [contact.display_name for contact in store.list_contacts()] == ["Zara Abbott", "Ana Zielinski"]


def test_search_matches_name_email_and_digits_of_a_phone(store: ContactStore) -> None:
    store.put("one", card("Mira Vance", "mira@riverbank.test"))
    store.put("two", "BEGIN:VCARD\r\nFN:Tomas Lind\r\nTEL;TYPE=CELL:+1 (555) 019-2837\r\nEND:VCARD\r\n")

    assert [c.resource_name for c in store.list_contacts("riverbank")] == ["one"]
    assert [c.resource_name for c in store.list_contacts("5550192837")] == ["two"]
    assert [c.resource_name for c in store.list_contacts("MIRA")] == ["one"]
    assert store.list_contacts("nobody") == ()


def test_search_treats_wildcards_literally(store: ContactStore) -> None:
    store.put("one", card("Mira Vance"))
    assert store.list_contacts("%") == ()


def test_delete_leaves_a_tombstone_visible_to_sync(store: ContactStore) -> None:
    store.put("mira", card("Mira Vance"))
    baseline = store.change_seq()
    assert store.delete("mira") is True
    assert store.delete("mira") is False

    delta = store.changes_since(baseline)
    assert delta.changed == ()
    assert delta.deleted == ("mira",)
    assert delta.change_seq > baseline


def test_changes_since_reports_only_later_writes(store: ContactStore) -> None:
    store.put("one", card("Ana Zielinski"))
    checkpoint = store.change_seq()
    store.put("two", card("Bo Nilsen"))

    delta = store.changes_since(checkpoint)
    assert [contact.resource_name for contact in delta.changed] == ["two"]
    assert delta.deleted == ()


def test_recreating_a_deleted_resource_clears_its_tombstone(store: ContactStore) -> None:
    baseline = store.change_seq()
    store.put("mira", card("Mira Vance"))
    store.delete("mira")
    store.put("mira", card("Mira Vance"))

    delta = store.changes_since(baseline)
    assert delta.deleted == ()
    assert [contact.resource_name for contact in delta.changed] == ["mira"]


def test_get_many_keeps_the_requested_order_and_skips_misses(store: ContactStore) -> None:
    store.put("one", card("Ana Zielinski"))
    store.put("two", card("Bo Nilsen"))
    found = store.get_many(("two", "missing", "one"))
    assert [contact.resource_name for contact in found] == ["two", "one"]


def test_generated_passwords_are_distinct_and_typable() -> None:
    passwords = {generate_password() for _ in range(200)}
    assert len(passwords) == 200
    for password in passwords:
        assert len(password) == 23
        assert set(password) <= set("abcdefghjkmnpqrstuvwxyz23456789-")


def test_the_password_is_created_once_and_then_stable(database: Database) -> None:
    credentials = CredentialStore(database)
    first = credentials.carddav_password()
    assert credentials.carddav_password().value == first.value
    assert credentials.verify_carddav_password(first.value) is True
    assert credentials.verify_carddav_password("not-it") is False


def test_regenerating_replaces_the_password(database: Database) -> None:
    credentials = CredentialStore(database)
    original = credentials.carddav_password().value
    replacement = credentials.regenerate_carddav_password().value

    assert replacement != original
    assert credentials.verify_carddav_password(original) is False
    assert credentials.verify_carddav_password(replacement) is True
