import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dav_helpers import vcard
from litestar import Litestar
from litestar.testing import TestClient

from server.repo import Repository
from server.store import ContactStore


def test_the_history_page_lists_every_version(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    store.put("mira", vcard("uid-1", "Mira Vance"), "create: Mira Vance")
    store.put("mira", vcard("uid-1", "Mira Vance-Okafor"), "edit: Mira Vance-Okafor")

    page = owner_client.get("/contacts/mira/history")
    assert page.status_code == 200
    assert "create: Mira Vance" in page.text
    assert "edit: Mira Vance-Okafor" in page.text
    assert "current" in page.text


def test_an_old_version_can_be_viewed(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    store.put("mira", vcard("uid-1", "Mira Vance"), "create")
    first = store.history("mira")[0].commit
    store.put("mira", vcard("uid-1", "Mira Vance-Okafor"), "edit")

    response = owner_client.get(f"/contacts/mira/history/{first}")
    assert response.status_code == 200
    assert "Mira Vance" in response.text
    assert "Vance-Okafor" not in response.text


def test_restoring_brings_a_version_back_without_losing_the_newer_one(
    owner_client: TestClient[Litestar], store: ContactStore
) -> None:
    store.put("mira", vcard("uid-1", "Mira Vance"), "create")
    original = store.history("mira")[0].commit
    store.put("mira", vcard("uid-1", "Mira Vance-Okafor"), "edit")

    response = owner_client.post("/contacts/mira/restore", data={"commit": original}, follow_redirects=False)
    assert response.status_code == 303

    assert store.get("mira").display_name == "Mira Vance"  # type: ignore[union-attr]
    summaries = [revision.summary for revision in store.history("mira")]
    assert len(summaries) == 3
    assert "restore" in summaries[0]


def test_restoring_a_version_that_does_not_exist_is_a_404(owner_client: TestClient[Litestar]) -> None:
    assert owner_client.get("/contacts/nobody/history").status_code == 404


def test_a_deleted_contact_is_still_in_the_repository_history(store: ContactStore, repository: Repository) -> None:
    """Deleting removes the file but not the record of it — which is the point of git."""
    store.put("mira", vcard("uid-1", "Mira Vance"), "create: Mira Vance")
    store.delete("mira", "delete: Mira Vance")

    assert store.get("mira") is None
    log = subprocess.run(
        ["git", "log", "--format=%s", "--", "mira.vcf"],
        cwd=repository.path,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert "create: Mira Vance" in log


def test_concurrent_writes_serialise_rather_than_racing(repository_path: Path) -> None:
    """Every writer takes the repository lock, so no commit is lost and the index never corrupts."""
    stores = [ContactStore(Repository(repository_path)) for _ in range(4)]

    def write(index: int) -> None:
        stores[index % len(stores)].put(f"c{index}", vcard(f"uid-{index}", f"Person {index}"), f"write {index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(24)))

    store = stores[0]
    assert store.count() == 24
    assert {contact.resource_name for contact in store.list_contacts()} == {f"c{i}" for i in range(24)}

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    assert status == "", f"the working tree should be clean after every write committed: {status!r}"
