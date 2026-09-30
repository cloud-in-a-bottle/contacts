import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
-- The address book itself lives in a git repository, not here.  All that is left for sqlite is the small
-- amount of app state that is not contact data and has no business being in version control.
CREATE TABLE IF NOT EXISTS app_secret (
    name       TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Database:
    """Owns the sqlite file and hands out one connection per thread.

    Every handler that touches the database runs in a worker thread (Litestar's ``sync_to_thread`` for the admin UI,
    an explicit ``to_thread`` hop for CardDAV), so connections are cached per thread rather than shared.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._local = threading.local()
        path.parent.mkdir(parents=True, exist_ok=True)
        # executescript() commits any open transaction before it runs, so schema setup cannot sit inside one.
        connection = self._connection()
        connection.executescript(SCHEMA)

    @property
    def path(self) -> Path:
        return self._path

    def _connection(self) -> sqlite3.Connection:
        existing: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if existing is not None:
            return existing
        connection = sqlite3.connect(self._path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        self._local.connection = connection
        return connection

    @contextmanager
    def reading(self) -> Iterator[sqlite3.Connection]:
        """A connection for read-only work.  Statements autocommit, so no transaction is opened."""
        yield self._connection()

    @contextmanager
    def writing(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside an IMMEDIATE transaction, committing on success and rolling back on any exception."""
        connection = self._connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    def close(self) -> None:
        existing: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if existing is not None:
            existing.close()
            self._local.connection = None
