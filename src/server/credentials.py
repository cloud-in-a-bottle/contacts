import hmac
import secrets
from datetime import UTC
from datetime import datetime

from server.db import Database
from server.models import Secret

CARDDAV_PASSWORD_NAME = "carddav_password"

# Unambiguous when read off a screen and retyped into a phone: no i/l/1, no o/0.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_GROUP_LENGTH = 5
_GROUP_COUNT = 4


def generate_password() -> str:
    """A ~99-bit password, grouped so it can be copied onto a phone without mistakes."""
    groups = ["".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LENGTH)) for _ in range(_GROUP_COUNT)]
    return "-".join(groups)


class CredentialStore:
    """The single CardDAV password.

    It is stored in the clear rather than hashed because the admin UI has to be able to show it again — the owner
    types it into each client they add.  The database lives in the app's private, backed-up data directory.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    def carddav_password(self) -> Secret:
        """The current password, generating one the first time it is asked for.

        The read comes first so that the common case — verifying a CardDAV request — does not take a write lock
        on every request.
        """
        with self._database.reading() as connection:
            row = connection.execute(
                "SELECT value, created_at FROM app_secret WHERE name = ?", (CARDDAV_PASSWORD_NAME,)
            ).fetchone()
        if row is not None:
            return Secret(value=row["value"], created_at=row["created_at"])

        with self._database.writing() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO app_secret (name, value, created_at) VALUES (?, ?, ?)",
                (CARDDAV_PASSWORD_NAME, generate_password(), datetime.now(UTC).isoformat(timespec="seconds")),
            )
            created = connection.execute(
                "SELECT value, created_at FROM app_secret WHERE name = ?", (CARDDAV_PASSWORD_NAME,)
            ).fetchone()
        return Secret(value=created["value"], created_at=created["created_at"])

    def regenerate_carddav_password(self) -> Secret:
        created_at = datetime.now(UTC).isoformat(timespec="seconds")
        password = generate_password()
        with self._database.writing() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_secret (name, value, created_at) VALUES (?, ?, ?)",
                (CARDDAV_PASSWORD_NAME, password, created_at),
            )
        return Secret(value=password, created_at=created_at)

    def verify_carddav_password(self, candidate: str) -> bool:
        expected = self.carddav_password().value
        return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
