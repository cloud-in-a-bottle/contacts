import sqlite3

import attr


@attr.s(auto_attribs=True, frozen=True)
class Contact:
    """A stored address book entry.  ``vcard`` is authoritative; every other column is derived from it."""

    resource_name: str
    uid: str
    vcard: str
    etag: str
    display_name: str
    created_at: str
    updated_at: str
    change_seq: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Contact":
        return cls(
            resource_name=row["resource_name"],
            uid=row["uid"],
            vcard=row["vcard"],
            etag=row["etag"],
            display_name=row["display_name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            change_seq=row["change_seq"],
        )

    @property
    def href_name(self) -> str:
        return f"{self.resource_name}.vcf"


@attr.s(auto_attribs=True, frozen=True)
class WriteResult:
    contact: Contact
    was_created: bool


@attr.s(auto_attribs=True, frozen=True)
class SyncDelta:
    changed: tuple[Contact, ...]
    deleted: tuple[str, ...]
    change_seq: int


@attr.s(auto_attribs=True, frozen=True)
class Secret:
    value: str
    created_at: str
