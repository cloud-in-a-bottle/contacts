import re
from pathlib import Path

from dav_helpers import vcard
from litestar import Litestar
from litestar.testing import TestClient

from server.app import create_app
from server.config import Config
from server.store import ContactStore

BOOK = "/dav/addressbooks/owner/default/"


def new_contact_form(**overrides: str) -> dict[str, str]:
    form = {
        "formatted_name": "Mira Vance",
        "given": "Mira",
        "family": "Vance",
        "additional": "",
        "prefix": "",
        "suffix": "",
        "nickname": "",
        "organization": "Riverbank Press",
        "department": "",
        "job_title": "Editor",
        "email_value": "mira@riverbank.test",
        "email_label": "work",
        "phone_value": "+44 7700 900123",
        "phone_label": "mobile",
        "url_value": "",
        "url_label": "",
        "address_street": "12 Wharf Road",
        "address_city": "Bristol",
        "address_region": "",
        "address_postal_code": "BS1 4QD",
        "address_country": "United Kingdom",
        "address_label": "work",
        "birthday": "",
        "note": "Prefers email, not phone; mornings only.",
        "categories": "work, publishing",
    }
    form.update(overrides)
    return form


def created_resource_name(client: TestClient[Litestar]) -> str:
    response = client.post("/contacts", data=new_contact_form(), follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["Location"].rsplit("/", 1)[-1]


def test_the_admin_ui_is_closed_to_anyone_but_the_owner(anonymous_client: TestClient[Litestar]) -> None:
    for path in ("/", "/settings", "/contacts/new"):
        assert anonymous_client.get(path).status_code == 401, path
    assert anonymous_client.post("/contacts", data=new_contact_form()).status_code == 401


def test_health_needs_no_authentication(anonymous_client: TestClient[Litestar]) -> None:
    response = anonymous_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_dev_override_opens_the_ui_without_the_router(database_path: Path, repository_path: Path) -> None:
    app = create_app(
        Config(
            database_path=database_path,
            repository_path=repository_path,
            owner_username="alice",
            allow_unauthenticated_admin=True,
        )
    )
    with TestClient(app=app) as client:
        assert client.get("/").status_code == 200


def test_the_list_page_is_empty_to_begin_with(owner_client: TestClient[Litestar]) -> None:
    body = owner_client.get("/").text
    assert "No contacts yet" in body


def test_the_new_and_edit_forms_render(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    assert 'name="formatted_name"' in owner_client.get("/contacts/new").text

    store.put("mira", vcard("uid-1", "Mira Vance"), "test")
    edit = owner_client.get("/contacts/mira/edit")
    assert edit.status_code == 200
    assert 'value="Mira Vance"' in edit.text
    assert 'value="someone@example.test"' in edit.text


def test_creating_a_contact_from_the_form(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    resource_name = created_resource_name(owner_client)

    stored = store.get(resource_name)
    assert stored is not None
    assert stored.display_name == "Mira Vance"
    assert "EMAIL;TYPE=WORK:mira@riverbank.test" in stored.vcard
    assert "TEL;TYPE=CELL:+44 7700 900123" in stored.vcard
    assert "ADR;TYPE=WORK:;;12 Wharf Road;Bristol;;BS1 4QD;United Kingdom" in stored.vcard
    assert "NOTE:Prefers email\\, not phone\\; mornings only." in stored.vcard
    assert "CATEGORIES:work,publishing" in stored.vcard

    detail = owner_client.get(f"/contacts/{resource_name}").text
    assert "mira@riverbank.test" in detail
    assert "Riverbank Press" in detail
    assert "Bristol" in detail


def test_a_contact_needs_something_to_identify_it(owner_client: TestClient[Litestar]) -> None:
    blank = dict.fromkeys(new_contact_form(), "")
    response = owner_client.post("/contacts", data=blank)
    assert response.status_code == 422
    assert "Give the contact a name" in response.text


def test_editing_keeps_properties_the_form_does_not_cover(
    owner_client: TestClient[Litestar], store: ContactStore, dav_client: TestClient[Litestar]
) -> None:
    original = vcard("uid-1", "Mira Vance").replace("END:VCARD", "X-ABShowAs:COMPANY\r\nEND:VCARD")
    dav_client.put(f"{BOOK}mira.vcf", content=original)

    form = new_contact_form(formatted_name="Mira Vance-Okafor", family="Vance-Okafor")
    response = owner_client.post("/contacts/mira", data=form, follow_redirects=False)
    assert response.status_code == 303

    updated = store.get("mira")
    assert updated is not None
    assert updated.display_name == "Mira Vance-Okafor"
    assert "X-ABShowAs:COMPANY" in updated.vcard
    assert "UID:uid-1" in updated.vcard


def test_deleting_from_the_ui_removes_it_from_carddav_too(
    owner_client: TestClient[Litestar], dav_client: TestClient[Litestar]
) -> None:
    resource_name = created_resource_name(owner_client)
    assert dav_client.get(f"{BOOK}{resource_name}.vcf").status_code == 200

    response = owner_client.post(f"/contacts/{resource_name}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert dav_client.get(f"{BOOK}{resource_name}.vcf").status_code == 404
    assert owner_client.get(f"/contacts/{resource_name}").status_code == 404


def test_search_narrows_the_list(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    store.put("one", vcard("uid-1", "Ana Zielinski", "ana@riverbank.test"), "test")
    store.put("two", vcard("uid-2", "Bo Nilsen", "bo@other.test"), "test")

    body = owner_client.get("/", params={"q": "riverbank"}).text
    assert "Ana Zielinski" in body
    assert "Bo Nilsen" not in body


def test_contact_names_are_escaped_in_html(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    store.put("x", vcard("uid-1", "<script>alert(1)</script>"), "test")
    body = owner_client.get("/").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_vcard_can_be_downloaded(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    store.put("mira", vcard("uid-1", "Mira Vance"), "test")
    response = owner_client.get("/contacts/mira/vcard")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/vcard")
    assert response.text == vcard("uid-1", "Mira Vance")


def test_an_embedded_photo_is_served(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    store.put(
        "mira",
        f"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Mira Vance\r\nPHOTO;ENCODING=b;TYPE=PNG:{pixel}\r\nEND:VCARD\r\n",
        "test",
    )
    response = owner_client.get("/contacts/mira/photo")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG")
    assert '<img class="avatar" src="/contacts/mira/photo"' in owner_client.get("/").text


def test_a_contact_without_a_photo_has_no_photo_route(owner_client: TestClient[Litestar], store: ContactStore) -> None:
    store.put("mira", vcard("uid-1", "Mira Vance"), "test")
    assert owner_client.get("/contacts/mira/photo").status_code == 404


def test_settings_shows_the_carddav_password_and_urls(
    owner_client: TestClient[Litestar], carddav_password: str
) -> None:
    body = owner_client.get("/settings").text
    assert carddav_password in body
    assert "/dav/" in body
    assert "alice" in body


def test_the_settings_url_follows_the_forwarded_host(owner_client: TestClient[Litestar]) -> None:
    body = owner_client.get(
        "/settings", headers={"X-Forwarded-Host": "contacts.zone.example", "X-Forwarded-Proto": "https"}
    ).text
    assert "https://contacts.zone.example/dav/" in body


def test_regenerating_the_password_invalidates_the_old_one(
    owner_client: TestClient[Litestar], dav_client: TestClient[Litestar], carddav_password: str
) -> None:
    assert dav_client.request("PROPFIND", BOOK, headers={"Depth": "0"}).status_code == 207

    response = owner_client.post("/settings/regenerate-password", follow_redirects=False)
    assert response.status_code == 303

    assert dav_client.request("PROPFIND", BOOK, headers={"Depth": "0"}).status_code == 401
    body = owner_client.get("/settings?regenerated=true").text
    assert carddav_password not in body
    assert re.search(r"class=\"secret\">([a-z2-9]{5}-){3}[a-z2-9]{5}<", body)
