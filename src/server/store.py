import sqlite3
from collections.abc import Sequence
from datetime import UTC
from datetime import datetime

from server.db import Database
from server.models import Contact
from server.models import Fingerprints
from server.models import SyncDelta
from server.models import WriteResult
from server.vcard.summary import summarize

_COLUMNS = "resource_name, uid, vcard, etag, display_name, created_at, updated_at, change_seq"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ContactStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def change_seq(self) -> int:
        with self._database.reading() as connection:
            row = connection.execute("SELECT change_seq FROM collection_state WHERE id = 1").fetchone()
        return int(row["change_seq"])

    def count(self) -> int:
        with self._database.reading() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM contact").fetchone()
        return int(row["total"])

    def list_contacts(self, search: str = "") -> tuple[Contact, ...]:
        query = f"SELECT {_COLUMNS} FROM contact"
        parameters: tuple[str, ...] = ()
        if search.strip():
            query += " WHERE search_text LIKE ? ESCAPE '\\'"
            parameters = (f"%{_escape_like(search.strip().lower())}%",)
        query += " ORDER BY sort_key, resource_name"
        with self._database.reading() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(Contact.from_row(row) for row in rows)

    def get(self, resource_name: str) -> Contact | None:
        with self._database.reading() as connection:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM contact WHERE resource_name = ?", (resource_name,)
            ).fetchone()
        return Contact.from_row(row) if row is not None else None

    def get_many(self, resource_names: tuple[str, ...]) -> tuple[Contact, ...]:
        if not resource_names:
            return ()
        placeholders = ",".join("?" for _ in resource_names)
        with self._database.reading() as connection:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM contact WHERE resource_name IN ({placeholders})", resource_names
            ).fetchall()
        found = {row["resource_name"]: Contact.from_row(row) for row in rows}
        return tuple(found[name] for name in resource_names if name in found)

    def put(self, resource_name: str, vcard_text: str) -> WriteResult:
        """Create or replace a contact, storing ``vcard_text`` verbatim and deriving the indexed columns from it."""
        summary = summarize(vcard_text, fallback_uid=resource_name)
        timestamp = _now()
        with self._database.writing() as connection:
            change_seq = _bump_change_seq(connection)
            existing = connection.execute(
                "SELECT created_at FROM contact WHERE resource_name = ?", (resource_name,)
            ).fetchone()
            created_at = existing["created_at"] if existing is not None else timestamp
            connection.execute(
                """
                INSERT INTO contact (resource_name, uid, vcard, etag, display_name, sort_key, search_text,
                                     created_at, updated_at, change_seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (resource_name) DO UPDATE SET
                    uid = excluded.uid, vcard = excluded.vcard, etag = excluded.etag,
                    display_name = excluded.display_name, sort_key = excluded.sort_key,
                    search_text = excluded.search_text, updated_at = excluded.updated_at,
                    change_seq = excluded.change_seq
                """,
                (
                    resource_name,
                    summary.uid,
                    vcard_text,
                    summary.etag,
                    summary.display_name,
                    summary.sort_key,
                    summary.search_text,
                    created_at,
                    timestamp,
                    change_seq,
                ),
            )
            # A resource that comes back after a delete must stop being reported as deleted.
            connection.execute("DELETE FROM tombstone WHERE resource_name = ?", (resource_name,))
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM contact WHERE resource_name = ?", (resource_name,)
            ).fetchone()
        return WriteResult(contact=Contact.from_row(row), was_created=existing is None)

    def put_many(self, cards: Sequence[tuple[str, str]]) -> tuple[Contact, ...]:
        """Write several contacts in one transaction, under a single change sequence.

        Importing a file of a few thousand cards one transaction at a time would be slow and would flood a
        syncing client with a distinct change per card; one bump means one delta covering the whole import.
        """
        if not cards:
            return ()
        timestamp = _now()
        with self._database.writing() as connection:
            change_seq = _bump_change_seq(connection)
            for resource_name, vcard_text in cards:
                summary = summarize(vcard_text, fallback_uid=resource_name)
                existing = connection.execute(
                    "SELECT created_at FROM contact WHERE resource_name = ?", (resource_name,)
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO contact (resource_name, uid, vcard, etag, display_name, sort_key, search_text,
                                         created_at, updated_at, change_seq)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (resource_name) DO UPDATE SET
                        uid = excluded.uid, vcard = excluded.vcard, etag = excluded.etag,
                        display_name = excluded.display_name, sort_key = excluded.sort_key,
                        search_text = excluded.search_text, updated_at = excluded.updated_at,
                        change_seq = excluded.change_seq
                    """,
                    (
                        resource_name,
                        summary.uid,
                        vcard_text,
                        summary.etag,
                        summary.display_name,
                        summary.sort_key,
                        summary.search_text,
                        existing["created_at"] if existing is not None else timestamp,
                        timestamp,
                        change_seq,
                    ),
                )
                connection.execute("DELETE FROM tombstone WHERE resource_name = ?", (resource_name,))
            placeholders = ",".join("?" for _ in cards)
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM contact WHERE resource_name IN ({placeholders})",
                tuple(resource_name for resource_name, _ in cards),
            ).fetchall()
        return tuple(Contact.from_row(row) for row in rows)

    def fingerprints(self) -> Fingerprints:
        """The UIDs and content hashes already stored, used to spot duplicates during an import."""
        with self._database.reading() as connection:
            rows = connection.execute("SELECT uid, etag FROM contact").fetchall()
        return Fingerprints(
            uids=frozenset(row["uid"] for row in rows if row["uid"]),
            etags=frozenset(row["etag"] for row in rows),
        )

    def delete(self, resource_name: str) -> bool:
        with self._database.writing() as connection:
            cursor = connection.execute("DELETE FROM contact WHERE resource_name = ?", (resource_name,))
            if cursor.rowcount == 0:
                return False
            change_seq = _bump_change_seq(connection)
            connection.execute(
                "INSERT OR REPLACE INTO tombstone (resource_name, change_seq, deleted_at) VALUES (?, ?, ?)",
                (resource_name, change_seq, _now()),
            )
        return True

    def changes_since(self, since_seq: int) -> SyncDelta:
        """Everything that changed after ``since_seq``, for a CardDAV sync-collection REPORT."""
        with self._database.reading() as connection:
            current = int(connection.execute("SELECT change_seq FROM collection_state WHERE id = 1").fetchone()[0])
            changed = connection.execute(
                f"SELECT {_COLUMNS} FROM contact WHERE change_seq > ? ORDER BY change_seq", (since_seq,)
            ).fetchall()
            deleted = connection.execute(
                "SELECT resource_name FROM tombstone WHERE change_seq > ? ORDER BY change_seq", (since_seq,)
            ).fetchall()
        return SyncDelta(
            changed=tuple(Contact.from_row(row) for row in changed),
            deleted=tuple(row["resource_name"] for row in deleted),
            change_seq=current,
        )


def _bump_change_seq(connection: sqlite3.Connection) -> int:
    connection.execute("UPDATE collection_state SET change_seq = change_seq + 1 WHERE id = 1")
    row = connection.execute("SELECT change_seq FROM collection_state WHERE id = 1").fetchone()
    return int(row["change_seq"])


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
