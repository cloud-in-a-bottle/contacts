import attr


@attr.s(auto_attribs=True, frozen=True)
class Contact:
    """A stored address book entry.

    ``vcard`` is the file's contents, byte for byte; everything else is derived from it or from the repository.
    ``etag`` is the git blob name of those bytes, so it is the same identifier git itself uses for them.
    """

    resource_name: str
    uid: str
    vcard: str
    etag: str
    display_name: str
    updated_at: str

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
    token: str


@attr.s(auto_attribs=True, frozen=True)
class Secret:
    value: str
    created_at: str


@attr.s(auto_attribs=True, frozen=True)
class Fingerprints:
    """What is already stored, for deciding whether an incoming card is a duplicate."""

    uids: frozenset[str]
    etags: frozenset[str]
