import base64
import re

import pytest
from dav_helpers import vcard
from litestar import Litestar
from litestar.testing import TestClient

from server.store import ContactStore

# Everything a request without the owner's session or the CardDAV password may get a non-error answer from.  Adding
# to this is a decision about what the public internet can see, so it should be deliberate.
_OPEN = {("/health", "GET"), ("/health", "HEAD"), ("/health", "OPTIONS")}
_WELL_KNOWN = "/.well-known/carddav"
_METHODS = ("GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "MKCOL")
_WRONG_CREDENTIALS = (
    {},
    {"Authorization": "Basic " + base64.b64encode(b"owner:").decode()},
    {"Authorization": "Basic " + base64.b64encode(b"owner:not-the-password").decode()},
    {"Authorization": "Bearer anything"},
    # Only the router may vouch for the owner, and only with exactly this value; it strips any inbound copy.
    {"X-OpenHost-Is-Owner": "1"},
    {"X_OpenHost_Is_Owner": "true"},
)


def _concrete_paths(app: Litestar) -> list[str]:
    """Every route the app serves, with path parameters filled in so the request reaches the handler."""
    paths = {re.sub(r"\{[^}]+\}", "mira", route.path) for route in app.routes}
    book = "/dav/addressbooks/owner/default/"
    return sorted(paths | {"/dav/", book, f"{book}mira.vcf", "/dav/../settings", f"{_WELL_KNOWN}/../../{book}"})


def test_nothing_answers_without_credentials(
    app: Litestar, anonymous_client: TestClient[Litestar], store: ContactStore
) -> None:
    store.put("mira", vcard("uid-mira", "Mira Vance"), "test")
    for path in _concrete_paths(app):
        for method in _METHODS:
            for headers in _WRONG_CREDENTIALS:
                response = anonymous_client.request(method, path, headers=headers, follow_redirects=False)
                where = f"{method} {path} {headers}"
                assert "Mira" not in response.text and "uid-mira" not in response.text, where
                if path.startswith(_WELL_KNOWN) and response.status_code == 301:
                    assert response.headers["Location"] == "/dav/", where
                    continue
                if (path, method) not in _OPEN:
                    assert response.status_code >= 400, where
    assert store.get("mira") is not None


@pytest.mark.parametrize(
    "path", ["/dav/addressbooks/owner/default/new.vcf", "/dav/addressbooks/owner/default/mira.vcf"]
)
def test_an_anonymous_write_changes_nothing(
    path: str, anonymous_client: TestClient[Litestar], store: ContactStore
) -> None:
    store.put("mira", vcard("uid-mira", "Mira Vance"), "test")
    before = store.token()
    assert anonymous_client.put(path, content=vcard("uid-x", "Intruder")).status_code == 401
    assert anonymous_client.delete(path).status_code == 401
    assert store.token() == before
