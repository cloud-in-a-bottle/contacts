import re
import threading
from collections.abc import Iterable
from collections.abc import Sequence
from datetime import UTC
from datetime import datetime

import attr

from server.models import Contact
from server.models import Fingerprints
from server.models import SyncDelta
from server.models import WriteResult
from server.naming import filename_for
from server.naming import is_safe_resource_name
from server.naming import resource_name_from_filename
from server.repo import Repository
from server.repo import Revision
from server.repo import blob_sha
from server.vcard.summary import summarize


@attr.s(auto_attribs=True, frozen=True)
class IndexEntry:
    """The derived facts about one contact — everything the list page shows, so it never re-parses a card."""

    resource_name: str
    uid: str
    etag: str
    display_name: str
    sort_key: str
    search_text: str
    subtitle: str
    initials: str
    has_photo: bool
    updated_at: str


@attr.s(auto_attribs=True, frozen=True)
class Index:
    token: str
    entries: tuple[IndexEntry, ...]


class ContactStore:
    """The address book, stored as one ``.vcf`` file per contact in a git repository.

    git supplies what would otherwise need bookkeeping: a commit is the sync token, a blob name is the ETag, and
    ``git diff`` between two commits is the delta a CardDAV client asks for — including deletions, so there is no
    tombstone table to keep forever.

    Parsing every card to sort and search it is the one thing git cannot do, so the derived fields are cached
    against the current commit.  When HEAD moves the cache is brought forward from the diff rather than rebuilt,
    so a single write costs one card's worth of parsing and not the whole address book's.
    """

    def __init__(self, repository: Repository) -> None:
        self._repository = repository
        self._index: Index | None = None
        self._index_lock = threading.Lock()

    @property
    def repository(self) -> Repository:
        return self._repository

    def token(self) -> str:
        """The commit that is currently HEAD — the collection's version, as a CardDAV client sees it."""
        return self._repository.head()

    def _current_index(self) -> Index:
        token = self.token()
        cached = self._index
        if cached is not None and cached.token == token:
            return cached
        with self._index_lock:
            cached = self._index
            if cached is not None and cached.token == token:
                return cached
            if cached is not None and self._repository.is_known_commit(cached.token):
                index = self._advance_index(cached, token)
            else:
                index = self._build_index(token)
            self._index = index
            return index

    def _advance_index(self, cached: Index, token: str) -> Index:
        """Bring a cached index forward to ``token`` by re-reading only what the diff says changed."""
        entries = {entry.resource_name: entry for entry in cached.entries}
        for change in self._repository.changes_since(cached.token):
            resource_name = resource_name_from_filename(change.path)
            if resource_name is None:
                continue
            entries.pop(resource_name, None)
            if change.is_deleted:
                continue
            entry = self._read_entry(resource_name)
            if entry is not None:
                entries[resource_name] = entry
        return Index(token=token, entries=_sorted(entries.values()))

    def _build_index(self, token: str) -> Index:
        entries = [
            entry
            for filename, _ in self._repository.list_files()
            if (resource_name := resource_name_from_filename(filename)) is not None
            and (entry := self._read_entry(resource_name)) is not None
        ]
        return Index(token=token, entries=_sorted(entries))

    def _read_entry(self, resource_name: str) -> IndexEntry | None:
        filename = filename_for(resource_name)
        text = self._repository.read_file(filename)
        if text is None:
            return None
        summary = summarize(text, fallback_uid=resource_name)
        modified = (self._repository.path / filename).stat().st_mtime
        return IndexEntry(
            resource_name=resource_name,
            uid=summary.uid,
            etag=content_etag(text),
            display_name=summary.display_name,
            sort_key=summary.sort_key,
            search_text=summary.search_text,
            subtitle=summary.subtitle,
            initials=summary.initials,
            has_photo=summary.has_photo,
            updated_at=datetime.fromtimestamp(modified, UTC).isoformat(timespec="seconds"),
        )

    def _contact_from(self, entry: IndexEntry) -> Contact | None:
        text = self._repository.read_file(filename_for(entry.resource_name))
        if text is None:
            return None
        return Contact(
            resource_name=entry.resource_name,
            uid=entry.uid,
            vcard=text,
            etag=entry.etag,
            display_name=entry.display_name,
            updated_at=entry.updated_at,
        )

    def count(self) -> int:
        return len(self._current_index().entries)

    def list_summaries(self, search: str = "") -> tuple[IndexEntry, ...]:
        """Everything the list page needs, straight from the cache — no files read, no cards parsed."""
        needle = search.strip().lower()
        entries = self._current_index().entries
        if not needle:
            return entries
        return tuple(entry for entry in entries if needle in entry.search_text)

    def list_contacts(self, search: str = "") -> tuple[Contact, ...]:
        """The same, but with each card's text, for the callers that actually need it."""
        return tuple(
            contact for entry in self.list_summaries(search) if (contact := self._contact_from(entry)) is not None
        )

    def get(self, resource_name: str) -> Contact | None:
        if not is_safe_resource_name(resource_name):
            return None
        for entry in self._current_index().entries:
            if entry.resource_name == resource_name:
                return self._contact_from(entry)
        return None

    def get_many(self, resource_names: tuple[str, ...]) -> tuple[Contact, ...]:
        found = {entry.resource_name: entry for entry in self._current_index().entries}
        contacts = (found.get(name) for name in resource_names)
        return tuple(
            contact for entry in contacts if entry is not None and (contact := self._contact_from(entry)) is not None
        )

    def fingerprints(self) -> Fingerprints:
        entries = self._current_index().entries
        return Fingerprints(
            uids=frozenset(entry.uid for entry in entries if entry.uid),
            etags=frozenset(entry.etag for entry in entries),
        )

    def put(self, resource_name: str, vcard_text: str, message: str) -> WriteResult:
        """Write one contact and commit it.  ``vcard_text`` is stored exactly as given."""
        with self._repository.locked():
            existed = self.get(resource_name) is not None
            self._repository.write_files([(filename_for(resource_name), vcard_text)])
            self._repository.commit(message)
        contact = self.get(resource_name)
        assert contact is not None, "the contact was just written"
        return WriteResult(contact=contact, was_created=not existed)

    def put_many(self, cards: Sequence[tuple[str, str]], message: str) -> tuple[Contact, ...]:
        """Write several contacts under a single commit, so an import is one change rather than hundreds."""
        if not cards:
            return ()
        with self._repository.locked():
            self._repository.write_files([(filename_for(name), text) for name, text in cards])
            self._repository.commit(message)
        return self.get_many(tuple(name for name, _ in cards))

    def delete(self, resource_name: str, message: str) -> bool:
        if not is_safe_resource_name(resource_name):
            return False
        with self._repository.locked():
            if not self._repository.remove_file(filename_for(resource_name)):
                return False
            self._repository.commit(message)
        return True

    def changes_since(self, token: str) -> SyncDelta | None:
        """The delta between ``token`` and now, or None if that commit is not one this repository knows."""
        if not self._repository.is_known_commit(token):
            return None
        head = self.token()
        changed: list[Contact] = []
        deleted: list[str] = []
        for change in self._repository.changes_since(token):
            resource_name = resource_name_from_filename(change.path)
            if resource_name is None:
                continue
            if change.is_deleted:
                deleted.append(resource_name)
                continue
            contact = self.get(resource_name)
            if contact is None:
                # Present in the diff but gone from the tree: report it as a deletion rather than lie.
                deleted.append(resource_name)
            else:
                changed.append(contact)
        return SyncDelta(changed=tuple(changed), deleted=tuple(deleted), token=head)

    def history(self, resource_name: str) -> tuple[Revision, ...]:
        if not is_safe_resource_name(resource_name):
            return ()
        return self._repository.history(filename_for(resource_name))

    def version(self, resource_name: str, commit: str) -> str | None:
        """The text of a contact as of some past commit."""
        if not is_safe_resource_name(resource_name) or not self._repository.is_known_commit(commit):
            return None
        return self._repository.show(commit, filename_for(resource_name))


def _sorted(entries: Iterable[IndexEntry]) -> tuple[IndexEntry, ...]:
    return tuple(sorted(entries, key=lambda entry: (entry.sort_key, entry.resource_name)))


def content_etag(text: str) -> str:
    """The ETag for a card's bytes: the name git gives that blob, so the two can never disagree."""
    return f'"{blob_sha(text.encode("utf-8"))}"'


_UNSAFE_MESSAGE = re.compile(r"[\x00-\x1f]")


def describe(action: str, subject: str) -> str:
    """A one-line commit message, with anything that would break the log flattened out of the subject."""
    cleaned = _UNSAFE_MESSAGE.sub(" ", subject).strip()
    return f"{action}: {cleaned}" if cleaned else action
