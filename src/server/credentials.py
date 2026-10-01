import hmac
import math
import secrets
from datetime import UTC
from datetime import datetime
from pathlib import Path

from server.db import Database
from server.models import Secret

CARDDAV_PASSWORD_NAME = "carddav_password"

WORDLIST_PATH = Path(__file__).parent / "wordlist.txt"
WORD_COUNT = 5
SEPARATOR = "-"

_MINIMUM_WORDLIST_SIZE = 1000


def _load_words() -> tuple[str, ...]:
    words = tuple(
        stripped
        for line in WORDLIST_PATH.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    )
    if len(words) < _MINIMUM_WORDLIST_SIZE:
        raise RuntimeError(
            f"{WORDLIST_PATH} holds only {len(words)} words; a password drawn from so few would be guessable"
        )
    return words


WORDS = _load_words()


def password_entropy_bits() -> float:
    """How much guesswork a password represents, given words are drawn without replacement."""
    permutations = math.prod(len(WORDS) - offset for offset in range(WORD_COUNT))
    return math.log2(permutations)


def generate_password() -> str:
    """Five random words, hyphenated — ``cider-shrug-mango-vowel-tusk``.

    Words rather than characters because this is read off the settings page and typed by hand into a phone's
    account settings, where a string of random letters invites mistakes.

    Drawn without replacement: repeating a word would cost no meaningful entropy (the list is long enough that
    the two differ in the third decimal place of a bit) but a password like ``cat-cat-shoe-dog-fox`` reads as a
    bug.  ``SystemRandom`` is seeded from the OS, so the sampling is still cryptographically sound.
    """
    return SEPARATOR.join(secrets.SystemRandom().sample(WORDS, k=WORD_COUNT))


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
