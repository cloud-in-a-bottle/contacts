import subprocess
from pathlib import Path

import pytest

from server.credentials import CredentialStore
from server.credentials import generate_password
from server.db import Database
from server.naming import is_safe_resource_name
from server.repo import Repository
from server.store import ContactStore
from server.store import content_etag
from server.store import describe


def card(name: str, email: str = "") -> str:
    given, _, family = name.partition(" ")
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{name}", f"N:{family or given};{given};;;"]
    if email:
        lines.append(f"EMAIL;TYPE=WORK:{email}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def git(repository: Repository, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=repository.path, capture_output=True, text=True, check=True, timeout=60
    ).stdout


def test_put_then_get_round_trips_the_card_verbatim(store: ContactStore) -> None:
    text = card("Mira Vance", "mira@example.test")
    result = store.put("mira", text, "test")
    assert result.was_created is True

    stored = store.get("mira")
    assert stored is not None
    assert stored.vcard == text
    assert stored.display_name == "Mira Vance"
    assert stored.etag == result.contact.etag


def test_contacts_are_plain_files_on_disk(store: ContactStore, repository: Repository) -> None:
    """The point of the whole design: the address book is legible without this app."""
    text = card("Mira Vance")
    store.put("mira", text, "test")

    on_disk = repository.path / "mira.vcf"
    assert on_disk.is_file()
    with open(on_disk, encoding="utf-8", newline="") as handle:
        assert handle.read() == text, "CRLFs must survive; they are part of the bytes the ETag names"


def test_the_etag_is_the_git_blob_name(store: ContactStore, repository: Repository) -> None:
    text = card("Mira Vance")
    store.put("mira", text, "test")

    from_git = git(repository, "rev-parse", "HEAD:mira.vcf").strip()
    assert store.get("mira").etag == f'"{from_git}"'  # type: ignore[union-attr]
    assert content_etag(text) == f'"{from_git}"'


def test_the_etag_changes_with_content() -> None:
    first = content_etag(card("Mira Vance"))
    second = content_etag(card("Mira Vance-Okafor"))
    assert first != second
    assert first.startswith('"') and first.endswith('"')


def test_put_twice_updates_rather_than_duplicating(store: ContactStore) -> None:
    store.put("mira", card("Mira Vance"), "test")
    second = store.put("mira", card("Mira Vance-Okafor"), "test")

    assert second.was_created is False
    assert store.count() == 1
    assert store.get("mira").display_name == "Mira Vance-Okafor"  # type: ignore[union-attr]


def test_rewriting_identical_bytes_is_not_a_change(store: ContactStore) -> None:
    """HEAD is the sync token, so a no-op write must not tell every client to resynchronise."""
    text = card("Mira Vance")
    store.put("mira", text, "first")
    token = store.token()

    result = store.put("mira", text, "second")
    assert result.was_created is False
    assert store.token() == token


def test_writes_land_as_commits_with_readable_messages(store: ContactStore, repository: Repository) -> None:
    store.put("mira", card("Mira Vance"), describe("create", "Mira Vance"))
    store.put("mira", card("Mira Vance-Okafor"), describe("edit", "Mira Vance-Okafor"))
    store.delete("mira", describe("delete", "Mira Vance-Okafor"))

    log = git(repository, "log", "--format=%s").splitlines()
    assert log[:3] == ["delete: Mira Vance-Okafor", "edit: Mira Vance-Okafor", "create: Mira Vance"]


def test_contacts_sort_by_family_name(store: ContactStore) -> None:
    store.put("b", card("Zara Abbott"), "test")
    store.put("a", card("Ana Zielinski"), "test")
    assert [contact.display_name for contact in store.list_contacts()] == ["Zara Abbott", "Ana Zielinski"]


def test_search_matches_name_email_and_digits_of_a_phone(store: ContactStore) -> None:
    store.put("one", card("Mira Vance", "mira@riverbank.test"), "test")
    store.put("two", "BEGIN:VCARD\r\nFN:Tomas Lind\r\nTEL;TYPE=CELL:+1 (555) 019-2837\r\nEND:VCARD\r\n", "test")

    assert [c.resource_name for c in store.list_contacts("riverbank")] == ["one"]
    assert [c.resource_name for c in store.list_contacts("5550192837")] == ["two"]
    assert [c.resource_name for c in store.list_contacts("MIRA")] == ["one"]
    assert store.list_contacts("nobody") == ()
    assert store.list_contacts("%") == (), "a search is a plain substring, not a pattern"


def test_get_many_keeps_the_requested_order_and_skips_misses(store: ContactStore) -> None:
    store.put("one", card("Ana Zielinski"), "test")
    store.put("two", card("Bo Nilsen"), "test")
    found = store.get_many(("two", "missing", "one"))
    assert [contact.resource_name for contact in found] == ["two", "one"]


def test_delete_removes_the_file_and_shows_up_in_the_delta(store: ContactStore, repository: Repository) -> None:
    store.put("mira", card("Mira Vance"), "test")
    token = store.token()

    assert store.delete("mira", "test") is True
    assert store.delete("mira", "test") is False
    assert not (repository.path / "mira.vcf").exists()

    delta = store.changes_since(token)
    assert delta is not None
    assert delta.changed == ()
    assert delta.deleted == ("mira",)


def test_changes_since_reports_only_later_writes(store: ContactStore) -> None:
    store.put("one", card("Ana Zielinski"), "test")
    token = store.token()
    store.put("two", card("Bo Nilsen"), "test")

    delta = store.changes_since(token)
    assert delta is not None
    assert [contact.resource_name for contact in delta.changed] == ["two"]
    assert delta.deleted == ()
    assert delta.token == store.token()


def test_a_contact_deleted_and_recreated_reads_as_a_change(store: ContactStore) -> None:
    token = store.token()
    store.put("mira", card("Mira Vance"), "test")
    store.delete("mira", "test")
    store.put("mira", card("Mira Vance"), "test")

    delta = store.changes_since(token)
    assert delta is not None
    assert delta.deleted == ()
    assert [contact.resource_name for contact in delta.changed] == ["mira"]


def test_an_unknown_token_is_refused_rather_than_guessed(store: ContactStore) -> None:
    assert store.changes_since("0" * 40) is None
    assert store.changes_since("not-a-commit") is None
    assert store.changes_since("") is None


def test_history_records_every_version_and_can_restore_one(store: ContactStore) -> None:
    store.put("mira", card("Mira Vance"), describe("create", "Mira Vance"))
    store.put("mira", card("Mira Vance-Okafor"), describe("edit", "Mira Vance-Okafor"))

    revisions = store.history("mira")
    assert [revision.summary for revision in revisions] == ["edit: Mira Vance-Okafor", "create: Mira Vance"]

    original = store.version("mira", revisions[-1].commit)
    assert original == card("Mira Vance")

    store.put("mira", original or "", describe("restore", "Mira Vance"))
    assert store.get("mira").display_name == "Mira Vance"  # type: ignore[union-attr]
    # Restoring adds to the history rather than rewinding it, so the undone version is still reachable.
    assert len(store.history("mira")) == 3


def test_a_version_that_does_not_exist_is_reported_as_missing(store: ContactStore) -> None:
    store.put("mira", card("Mira Vance"), "test")
    assert store.version("mira", "0" * 40) is None
    assert store.version("nobody", store.token()) is None


@pytest.mark.parametrize("name", ["..", ".", ".git", ".hidden", "a/b", "a\\b", "", "x" * 201])
def test_names_that_would_escape_the_repository_are_refused(name: str, store: ContactStore) -> None:
    assert is_safe_resource_name(name) is False
    assert store.get(name) is None
    assert store.delete(name, "test") is False


def test_ordinary_awkward_names_still_work(store: ContactStore, repository: Repository) -> None:
    for name in ("a b é", "with.dots", "UPPER", "-leading-dash"):
        assert is_safe_resource_name(name) is True
        store.put(name, card("Someone Here"), "test")
        assert store.get(name) is not None
        assert (repository.path / f"{name}.vcf").is_file()


def test_the_index_follows_a_write_made_by_another_instance(repository_path: Path) -> None:
    """Two stores over one repository: the cache is keyed on HEAD, so it cannot go stale."""
    first = ContactStore(Repository(repository_path))
    second = ContactStore(Repository(repository_path))

    assert first.count() == 0
    second.put("mira", card("Mira Vance"), "test")
    assert first.count() == 1
    assert first.get("mira") is not None


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


def test_no_contact_data_reaches_sqlite(store: ContactStore, database: Database) -> None:
    store.put("mira", card("Mira Vance", "mira@example.test"), "test")
    CredentialStore(database).carddav_password()

    with database.reading() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"app_secret"}
