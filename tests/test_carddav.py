import base64
from typing import cast

import anyio
from dav_helpers import CALSERVER
from dav_helpers import CARDDAV
from dav_helpers import DAV
from dav_helpers import parse_multistatus
from dav_helpers import propfind_body
from dav_helpers import vcard
from litestar import Litestar
from litestar.testing import TestClient
from litestar.types import ASGIApp
from litestar.types import Message
from litestar.types import Receive
from litestar.types import ReceiveMessage
from litestar.types.asgi_types import HTTPRequestEvent
from litestar.types.asgi_types import HTTPScope

from server.credentials import CredentialStore
from server.dav.asgi import MAX_REQUEST_BODY
from server.dav.asgi import make_dav_asgi
from server.dav.handler import DavHandler
from server.db import Database
from server.store import ContactStore

ROOT = "/dav/"
PRINCIPAL = "/dav/principals/owner/"
HOME = "/dav/addressbooks/owner/"
BOOK = "/dav/addressbooks/owner/default/"


def resource(name: str) -> str:
    return f"{BOOK}{name}.vcf"


def test_unauthenticated_requests_are_challenged(anonymous_client: TestClient[Litestar]) -> None:
    response = anonymous_client.request("PROPFIND", BOOK, headers={"Depth": "0"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic realm=")


def test_a_wrong_password_is_rejected(anonymous_client: TestClient[Litestar]) -> None:
    credential = base64.b64encode(b"owner:wrong-password").decode()
    response = anonymous_client.request(
        "PROPFIND", BOOK, headers={"Authorization": f"Basic {credential}", "Depth": "0"}
    )
    assert response.status_code == 401


def test_any_username_is_accepted_with_the_right_password(
    anonymous_client: TestClient[Litestar], carddav_password: str
) -> None:
    credential = base64.b64encode(f"whoever:{carddav_password}".encode()).decode()
    response = anonymous_client.request(
        "PROPFIND", BOOK, headers={"Authorization": f"Basic {credential}", "Depth": "0"}
    )
    assert response.status_code == 207


def test_the_owners_browser_session_is_accepted_without_basic_auth(
    owner_client: TestClient[Litestar],
) -> None:
    assert owner_client.request("PROPFIND", BOOK, headers={"Depth": "0"}).status_code == 207


def test_well_known_redirects_without_authentication(anonymous_client: TestClient[Litestar]) -> None:
    response = anonymous_client.get("/.well-known/carddav", follow_redirects=False)
    assert response.status_code == 301
    assert response.headers["Location"] == ROOT


def test_options_advertises_carddav(dav_client: TestClient[Litestar]) -> None:
    response = dav_client.request("OPTIONS", BOOK)
    assert response.status_code == 200
    assert "addressbook" in response.headers["DAV"]
    assert "REPORT" in response.headers["Allow"]


def test_discovery_walks_from_the_root_to_the_address_book(dav_client: TestClient[Litestar]) -> None:
    root = dav_client.request(
        "PROPFIND", ROOT, headers={"Depth": "0"}, content=propfind_body((DAV, "current-user-principal"))
    )
    assert root.status_code == 207
    principal_href = (
        parse_multistatus(root.content)
        .by_href(ROOT)
        .properties[f"{{{DAV}}}current-user-principal"]
        .findtext(f"{{{DAV}}}href")
    )
    assert principal_href == PRINCIPAL

    principal = dav_client.request(
        "PROPFIND",
        principal_href,
        headers={"Depth": "0"},
        content=propfind_body((CARDDAV, "addressbook-home-set")),
    )
    home_href = (
        parse_multistatus(principal.content)
        .by_href(PRINCIPAL)
        .properties[f"{{{CARDDAV}}}addressbook-home-set"]
        .findtext(f"{{{DAV}}}href")
    )
    assert home_href == HOME

    home = dav_client.request(
        "PROPFIND", home_href, headers={"Depth": "1"}, content=propfind_body((DAV, "resourcetype"))
    )
    listing = parse_multistatus(home.content)
    assert BOOK in listing.hrefs
    assert listing.by_href(BOOK).properties[f"{{{DAV}}}resourcetype"].find(f"{{{CARDDAV}}}addressbook") is not None


def test_put_creates_a_contact_and_get_returns_it_byte_for_byte(dav_client: TestClient[Litestar]) -> None:
    body = vcard("uid-1", "Mira Vance")
    created = dav_client.put(resource("mira"), content=body, headers={"Content-Type": "text/vcard"})
    assert created.status_code == 201
    assert created.headers["ETag"]

    fetched = dav_client.get(resource("mira"))
    assert fetched.status_code == 200
    assert fetched.text == body
    assert fetched.headers["Content-Type"].startswith("text/vcard")
    assert fetched.headers["ETag"] == created.headers["ETag"]


def test_put_over_an_existing_contact_returns_no_content(dav_client: TestClient[Litestar]) -> None:
    dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance"))
    updated = dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance-Okafor"))
    assert updated.status_code == 204
    assert "Vance-Okafor" in dav_client.get(resource("mira")).text


def test_if_none_match_star_refuses_to_overwrite(dav_client: TestClient[Litestar]) -> None:
    dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance"))
    clash = dav_client.put(resource("mira"), content=vcard("uid-1", "Someone Else"), headers={"If-None-Match": "*"})
    assert clash.status_code == 412
    assert "Mira Vance" in dav_client.get(resource("mira")).text


def test_if_match_guards_against_a_lost_update(dav_client: TestClient[Litestar]) -> None:
    created = dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance"))
    stale = created.headers["ETag"]
    dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance II"))

    rejected = dav_client.put(resource("mira"), content=vcard("uid-1", "Third Write"), headers={"If-Match": stale})
    assert rejected.status_code == 412

    current = dav_client.get(resource("mira")).headers["ETag"]
    accepted = dav_client.put(resource("mira"), content=vcard("uid-1", "Third Write"), headers={"If-Match": current})
    assert accepted.status_code == 204


def test_get_honours_if_none_match(dav_client: TestClient[Litestar]) -> None:
    etag = dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance")).headers["ETag"]
    assert dav_client.get(resource("mira"), headers={"If-None-Match": etag}).status_code == 304


def test_delete_removes_the_contact(dav_client: TestClient[Litestar]) -> None:
    dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance"))
    assert dav_client.delete(resource("mira")).status_code == 204
    assert dav_client.get(resource("mira")).status_code == 404
    assert dav_client.delete(resource("mira")).status_code == 404


def test_delete_honours_a_stale_if_match(dav_client: TestClient[Litestar]) -> None:
    stale = dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance")).headers["ETag"]
    dav_client.put(resource("mira"), content=vcard("uid-1", "Mira Vance II"))
    assert dav_client.delete(resource("mira"), headers={"If-Match": stale}).status_code == 412


def test_a_malformed_body_is_refused(dav_client: TestClient[Litestar]) -> None:
    assert dav_client.put(resource("x"), content=b"not a vcard").status_code == 400
    assert dav_client.put(resource("x"), content=b"\xff\xfe not utf-8").status_code == 400
    two_cards = (vcard("a", "A B") + vcard("c", "C D")).encode()
    assert dav_client.put(resource("x"), content=two_cards).status_code == 400


def test_an_unsupported_content_type_is_refused(dav_client: TestClient[Litestar]) -> None:
    response = dav_client.put(
        resource("x"), content=vcard("uid-1", "Mira Vance"), headers={"Content-Type": "image/png"}
    )
    assert response.status_code == 415


def test_the_address_book_itself_cannot_be_written_or_deleted(dav_client: TestClient[Litestar]) -> None:
    assert dav_client.put(BOOK, content=vcard("uid-1", "Mira Vance")).status_code == 405
    assert dav_client.delete(BOOK).status_code == 405


def test_propfind_depth_one_lists_every_contact(dav_client: TestClient[Litestar]) -> None:
    dav_client.put(resource("one"), content=vcard("uid-1", "Ana Zielinski"))
    dav_client.put(resource("two"), content=vcard("uid-2", "Bo Nilsen"))

    response = dav_client.request(
        "PROPFIND",
        BOOK,
        headers={"Depth": "1"},
        content=propfind_body((DAV, "getetag"), (DAV, "getcontenttype")),
    )
    listing = parse_multistatus(response.content)
    assert set(listing.hrefs) == {BOOK, resource("one"), resource("two")}
    assert listing.by_href(resource("one")).text(f"{{{DAV}}}getetag").startswith('"')


def test_propfind_reports_unknown_properties_as_missing(dav_client: TestClient[Litestar]) -> None:
    response = dav_client.request(
        "PROPFIND", BOOK, headers={"Depth": "0"}, content=propfind_body((DAV, "displayname"), (DAV, "nonsense"))
    )
    entry = parse_multistatus(response.content).by_href(BOOK)
    assert entry.text(f"{{{DAV}}}displayname") == "Contacts"
    assert entry.missing == (f"{{{DAV}}}nonsense",)


def test_the_ctag_changes_when_the_collection_changes(dav_client: TestClient[Litestar]) -> None:
    def ctag() -> str:
        response = dav_client.request(
            "PROPFIND", BOOK, headers={"Depth": "0"}, content=propfind_body((CALSERVER, "getctag"))
        )
        return parse_multistatus(response.content).by_href(BOOK).text(f"{{{CALSERVER}}}getctag")

    before = ctag()
    dav_client.put(resource("one"), content=vcard("uid-1", "Ana Zielinski"))
    assert ctag() != before


def test_addressbook_multiget_returns_address_data(dav_client: TestClient[Litestar]) -> None:
    dav_client.put(resource("one"), content=vcard("uid-1", "Ana Zielinski"))
    dav_client.put(resource("two"), content=vcard("uid-2", "Bo Nilsen"))

    body = f"""<?xml version="1.0"?>
    <C:addressbook-multiget xmlns:D="DAV:" xmlns:C="{CARDDAV}">
      <D:prop><D:getetag/><C:address-data/></D:prop>
      <D:href>{resource("one")}</D:href>
      <D:href>{resource("missing")}</D:href>
    </C:addressbook-multiget>"""
    response = dav_client.request("REPORT", BOOK, content=body, headers={"Depth": "1"})
    assert response.status_code == 207

    listing = parse_multistatus(response.content)
    assert "Ana Zielinski" in listing.by_href(resource("one")).text(f"{{{CARDDAV}}}address-data")
    assert listing.by_href(resource("missing")).status == "HTTP/1.1 404 Not Found"


def test_addressbook_query_filters_by_text(dav_client: TestClient[Litestar]) -> None:
    dav_client.put(resource("one"), content=vcard("uid-1", "Ana Zielinski", "ana@riverbank.test"))
    dav_client.put(resource("two"), content=vcard("uid-2", "Bo Nilsen", "bo@other.test"))

    body = f"""<?xml version="1.0"?>
    <C:addressbook-query xmlns:D="DAV:" xmlns:C="{CARDDAV}">
      <D:prop><D:getetag/></D:prop>
      <C:filter>
        <C:prop-filter name="EMAIL">
          <C:text-match match-type="contains">riverbank</C:text-match>
        </C:prop-filter>
      </C:filter>
    </C:addressbook-query>"""
    response = dav_client.request("REPORT", BOOK, content=body, headers={"Depth": "1"})
    assert parse_multistatus(response.content).hrefs == (resource("one"),)


def test_addressbook_query_without_a_filter_returns_everything(dav_client: TestClient[Litestar]) -> None:
    dav_client.put(resource("one"), content=vcard("uid-1", "Ana Zielinski"))
    body = f"""<?xml version="1.0"?>
    <C:addressbook-query xmlns:D="DAV:" xmlns:C="{CARDDAV}">
      <D:prop><D:getetag/></D:prop><C:filter/>
    </C:addressbook-query>"""
    response = dav_client.request("REPORT", BOOK, content=body, headers={"Depth": "1"})
    assert parse_multistatus(response.content).hrefs == (resource("one"),)


def _sync(client: TestClient[Litestar], token: str) -> tuple[str, ...]:
    body = f"""<?xml version="1.0"?>
    <D:sync-collection xmlns:D="DAV:" xmlns:C="{CARDDAV}">
      <D:sync-token>{token}</D:sync-token>
      <D:sync-level>1</D:sync-level>
      <D:prop><D:getetag/></D:prop>
    </D:sync-collection>"""
    response = client.request("REPORT", BOOK, content=body)
    assert response.status_code == 207
    return (response.content.decode(),)


def test_sync_collection_reports_creations_then_deletions(dav_client: TestClient[Litestar]) -> None:
    def sync(token: str) -> tuple[list[str], list[str], str]:
        body = f"""<?xml version="1.0"?>
        <D:sync-collection xmlns:D="DAV:" xmlns:C="{CARDDAV}">
          <D:sync-token>{token}</D:sync-token><D:sync-level>1</D:sync-level>
          <D:prop><D:getetag/></D:prop>
        </D:sync-collection>"""
        response = dav_client.request("REPORT", BOOK, content=body)
        assert response.status_code == 207
        parsed = parse_multistatus(response.content)
        changed = [e.href for e in parsed.entries if e.status is None]
        removed = [e.href for e in parsed.entries if e.status is not None]
        assert parsed.sync_token is not None
        return changed, removed, parsed.sync_token

    dav_client.put(resource("one"), content=vcard("uid-1", "Ana Zielinski"))
    changed, removed, token = sync("")
    assert changed == [resource("one")]
    assert removed == []

    changed, removed, token = sync(token)
    assert (changed, removed) == ([], [])

    dav_client.put(resource("two"), content=vcard("uid-2", "Bo Nilsen"))
    dav_client.delete(resource("one"))
    changed, removed, token = sync(token)
    assert changed == [resource("two")]
    assert removed == [resource("one")]


def test_an_unrecognised_sync_token_is_rejected(dav_client: TestClient[Litestar]) -> None:
    body = """<?xml version="1.0"?>
    <D:sync-collection xmlns:D="DAV:">
      <D:sync-token>urn:something:else</D:sync-token><D:prop><D:getetag/></D:prop>
    </D:sync-collection>"""
    response = dav_client.request("REPORT", BOOK, content=body)
    assert response.status_code == 409
    assert b"valid-sync-token" in response.content


def test_external_entities_are_not_expanded(dav_client: TestClient[Litestar]) -> None:
    body = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE p [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        '<D:propfind xmlns:D="DAV:"><D:prop><D:displayname>&xxe;</D:displayname></D:prop></D:propfind>'
    )
    response = dav_client.request("PROPFIND", BOOK, content=body, headers={"Depth": "0"})
    assert response.status_code == 400
    assert b"root:" not in response.content


def test_proppatch_is_accepted_but_changes_nothing(dav_client: TestClient[Litestar]) -> None:
    body = """<?xml version="1.0"?>
    <D:propertyupdate xmlns:D="DAV:">
      <D:set><D:prop><D:displayname>Renamed</D:displayname></D:prop></D:set>
    </D:propertyupdate>"""
    response = dav_client.request("PROPPATCH", BOOK, content=body)
    assert response.status_code == 207
    assert b"403 Forbidden" in response.content

    check = dav_client.request("PROPFIND", BOOK, headers={"Depth": "0"}, content=propfind_body((DAV, "displayname")))
    assert parse_multistatus(check.content).by_href(BOOK).text(f"{{{DAV}}}displayname") == "Contacts"


def test_unknown_methods_are_refused(dav_client: TestClient[Litestar]) -> None:
    assert dav_client.request("MKCOL", f"{BOOK}sub/").status_code == 404
    assert dav_client.request("MKCOL", BOOK).status_code == 405


def test_a_resource_name_with_odd_characters_round_trips(dav_client: TestClient[Litestar]) -> None:
    path = f"{BOOK}a%20b%20%C3%A9.vcf"
    assert dav_client.put(path, content=vcard("uid-1", "Mira Vance")).status_code == 201
    assert "Mira Vance" in dav_client.get(path).text

    listing = parse_multistatus(
        dav_client.request("PROPFIND", BOOK, headers={"Depth": "1"}, content=propfind_body((DAV, "getetag"))).content
    )
    assert path in listing.hrefs


def test_a_name_too_long_for_the_filesystem_is_refused_rather_than_failing(dav_client: TestClient[Litestar]) -> None:
    # 150 four-byte characters is within the 200-character limit but over the filesystem's 255 bytes.
    path = f"{BOOK}{'%F0%9F%98%80' * 150}.vcf"
    assert dav_client.put(path, content=vcard("uid-1", "Mira Vance")).status_code == 404


def test_an_oversized_declared_body_is_refused(dav_client: TestClient[Litestar]) -> None:
    response = dav_client.put(resource("big"), content=b"x" * (MAX_REQUEST_BODY + 1))
    assert response.status_code == 413
    assert dav_client.get(resource("big")).status_code == 404


def _status(app: ASGIApp, method: str, headers: list[tuple[bytes, bytes]], receive: Receive) -> int:
    statuses: list[int] = []

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    scope = cast(
        HTTPScope,
        {
            "type": "http",
            "method": method,
            "path": BOOK,
            "raw_path": BOOK.encode(),
            "query_string": b"",
            "headers": headers,
        },
    )
    anyio.run(app, scope, receive, send)
    return statuses[0]


def test_an_unauthenticated_body_is_never_read(store: ContactStore, database: Database) -> None:
    """The password is checked before the body, so a stranger cannot make the app buffer anything."""
    app = make_dav_asgi(DavHandler(store, CredentialStore(database), "alice"))

    async def receive() -> ReceiveMessage:
        raise AssertionError("the body of an unauthenticated request was read")

    assert _status(app, "PUT", [(b"content-length", b"999999999")], receive) == 401


def test_an_undeclared_body_is_cut_off_at_the_limit(store: ContactStore, database: Database) -> None:
    credentials = CredentialStore(database)
    app = make_dav_asgi(DavHandler(store, credentials, "alice"))
    authorization = base64.b64encode(f"x:{credentials.carddav_password().value}".encode())
    chunk = b"x" * (1024 * 1024)
    reads = 0

    async def receive() -> ReceiveMessage:
        nonlocal reads
        reads += 1
        message: HTTPRequestEvent = {"type": "http.request", "body": chunk, "more_body": True}
        return message

    assert _status(app, "REPORT", [(b"authorization", b"Basic " + authorization)], receive) == 413
    assert reads == MAX_REQUEST_BODY // len(chunk) + 1
