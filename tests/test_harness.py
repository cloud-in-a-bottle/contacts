"""End-to-end tests against the real OpenHost router.

These build the Dockerfile, run the app under podman per ``openhost.toml``, and front it with the router — the
only way to prove the two things the manifest claims: that the admin UI is closed unless the owner is logged in,
and that the CardDAV paths in ``public_paths`` are reachable (with their non-standard HTTP methods) without one.

They are marked ``containers`` and excluded from ``just test``; run them with ``just test-all`` on a host with
podman.
"""

import re
import shutil
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from dav_helpers import CARDDAV
from dav_helpers import DAV
from dav_helpers import parse_multistatus
from dav_helpers import propfind_body
from dav_helpers import vcard

pytestmark = pytest.mark.containers

BOOK = "/dav/addressbooks/owner/default/"


@pytest.fixture(scope="session")
def stack() -> Iterator[Any]:
    if shutil.which("podman") is None:
        pytest.skip("podman is not installed on this host")
    from openhost_test_harness import OpenhostStack  # noqa: PLC0415 -- only importable where the harness can run

    with OpenhostStack() as running_stack:
        yield running_stack


@pytest.fixture(scope="session")
def carddav_password(stack: Any) -> str:
    settings = stack.owner_session.get(f"{stack.url}/settings")
    settings.raise_for_status()
    match = re.search(r'class="secret">([^<]+)<', settings.text)
    assert match is not None, "the settings page did not show a CardDAV password"
    return match.group(1)


def test_health_is_served_to_the_container_probe(stack: Any) -> None:
    response = requests.get(f"{stack.app_url}/health", timeout=30)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_the_admin_ui_needs_the_owners_session(stack: Any) -> None:
    anonymous = requests.get(f"{stack.url}/", allow_redirects=False, timeout=30)
    assert anonymous.status_code in (302, 303, 307), anonymous.status_code
    assert "/login" in anonymous.headers.get("Location", "")

    owner = stack.owner_session.get(f"{stack.url}/")
    assert owner.status_code == 200
    assert "Contacts" in owner.text


def test_carddav_is_reachable_without_an_owner_session(stack: Any, carddav_password: str) -> None:
    """The point of ``public_paths``: a CardDAV client has no session cookie, only the generated password."""
    url = f"{stack.url}{BOOK}"

    challenged = requests.request("PROPFIND", url, headers={"Depth": "0"}, timeout=30)
    assert challenged.status_code == 401
    assert challenged.headers["WWW-Authenticate"].startswith("Basic realm=")

    authenticated = requests.request(
        "PROPFIND",
        url,
        headers={"Depth": "0"},
        data=propfind_body((DAV, "resourcetype")),
        auth=("owner", carddav_password),
        timeout=30,
    )
    assert authenticated.status_code == 207
    entry = parse_multistatus(authenticated.content).by_href(BOOK)
    assert entry.properties[f"{{{DAV}}}resourcetype"].find(f"{{{CARDDAV}}}addressbook") is not None


def test_a_contact_written_over_carddav_shows_up_in_the_admin_ui(stack: Any, carddav_password: str) -> None:
    auth = ("owner", carddav_password)
    put = requests.put(
        f"{stack.url}{BOOK}harness.vcf",
        data=vcard("harness-1", "Ines Bergstrom").encode("utf-8"),
        headers={"Content-Type": "text/vcard; charset=utf-8"},
        auth=auth,
        timeout=30,
    )
    assert put.status_code in (201, 204)

    listing = stack.owner_session.get(f"{stack.url}/")
    assert "Ines Bergstrom" in listing.text

    deleted = requests.delete(f"{stack.url}{BOOK}harness.vcf", auth=auth, timeout=30)
    assert deleted.status_code == 204


def test_the_well_known_path_redirects_without_authentication(stack: Any) -> None:
    response = requests.get(f"{stack.url}/.well-known/carddav", allow_redirects=False, timeout=30)
    assert response.status_code == 301
    assert response.headers["Location"] == "/dav/"
