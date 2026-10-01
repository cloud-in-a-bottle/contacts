"""Guards on the cost of listing an address book.

A 410-contact book with a few inline photos took ~1.7s to render its dashboard on a 0.25-core container, because
the list page re-parsed every card on every request and because unfolding a folded PHOTO was quadratic.  These
tests pin the two properties that fixed it, in ways that do not depend on how fast the machine running them is.
"""

import base64
import os
import time

from dav_helpers import vcard
from litestar import Litestar
from litestar.testing import TestClient

from server.repo import Repository
from server.store import ContactStore
from server.vcard.lines import unfold


def photo_card(uid: str, name: str, photo_bytes: int = 64 * 1024) -> str:
    encoded = base64.b64encode(os.urandom(photo_bytes)).decode()
    folded = [encoded[index : index + 73] for index in range(0, len(encoded), 73)]
    body = "\r\n".join([f"PHOTO;ENCODING=b;TYPE=JPEG:{folded[0]}", *(f" {chunk}" for chunk in folded[1:])])
    return vcard(uid, name).replace("END:VCARD", f"{body}\r\nEND:VCARD")


def test_unfolding_a_folded_photo_stays_linear() -> None:
    """A heavily folded value must not be re-copied per continuation line.

    The bound is loose enough not to be flaky — the linear version does this in tens of milliseconds — but the
    quadratic one took seconds, so a regression cannot slip past it.
    """
    folded = "".join(" " + "A" * 70 + "\r\n" for _ in range(40_000))
    text = f"BEGIN:VCARD\r\nPHOTO;ENCODING=b:{'A' * 70}\r\n{folded}END:VCARD\r\n"

    start = time.perf_counter()
    lines = unfold(text)
    elapsed = time.perf_counter() - start

    assert len(lines) == 3
    assert len(lines[1]) == 70 * 40_001 + len("PHOTO;ENCODING=b:")
    assert elapsed < 2.0, f"unfolding 40k folded lines took {elapsed:.1f}s, which means it is quadratic again"


def test_listing_reads_no_files_and_parses_no_cards(store: ContactStore, repository: Repository) -> None:
    """The list page must be answerable from the cached index alone."""
    store.put_many([(f"c{index}", vcard(f"uid-{index}", f"Person {index}")) for index in range(20)], "seed")
    store.put("photo", photo_card("uid-photo", "Pat Photo"), "seed photo")
    store.list_summaries()  # warm the index

    def explode(_: str) -> str:
        raise AssertionError("listing must not touch the files; everything it shows is in the index")

    repository.read_file = explode  # type: ignore[method-assign]
    entries = store.list_summaries()

    assert len(entries) == 21
    assert {entry.display_name for entry in entries} >= {"Pat Photo", "Person 0"}
    assert [entry.has_photo for entry in entries if entry.resource_name == "photo"] == [True]
    assert store.list_summaries("person 1")


def test_a_write_reindexes_only_what_changed(store: ContactStore) -> None:
    """Advancing the index off the diff is what keeps a bulk CardDAV sync from being quadratic."""
    store.put_many([(f"c{index}", vcard(f"uid-{index}", f"Person {index}")) for index in range(50)], "seed")
    store.list_summaries()

    read: list[str] = []
    original = store._read_entry

    def counting(resource_name: str):  # type: ignore[no-untyped-def]
        read.append(resource_name)
        return original(resource_name)

    store._read_entry = counting  # type: ignore[method-assign]

    store.put("c7", vcard("uid-7", "Person Seven Renamed"), "edit one")
    store.delete("c8", "delete one")
    entries = store.list_summaries()

    assert read == ["c7"], f"only the changed card should be re-read, not {len(read)}"
    assert len(entries) == 49
    assert {entry.display_name for entry in entries} >= {"Person Seven Renamed"}
    assert all(entry.resource_name != "c8" for entry in entries)


def test_a_restart_rebuilds_the_index_from_the_tree(store: ContactStore, repository_path: object) -> None:
    store.put_many([(f"c{index}", vcard(f"uid-{index}", f"Person {index}")) for index in range(10)], "seed")

    fresh = ContactStore(Repository(repository_path))  # type: ignore[arg-type]
    assert len(fresh.list_summaries()) == 10


def test_the_dashboard_renders_a_large_book_without_reparsing(
    owner_client: TestClient[Litestar], store: ContactStore
) -> None:
    cards = [(f"c{index}", vcard(f"uid-{index}", f"Person {index}")) for index in range(40)]
    cards.append(("photo", photo_card("uid-photo", "Pat Photo")))
    store.put_many(cards, "seed")

    body = owner_client.get("/").text
    assert body.count('class="avatar" src=') == 1, "only the card with a photo gets an <img>"
    assert body.count('class="avatar" aria-hidden') == 40
    assert "41 of 41" in body
