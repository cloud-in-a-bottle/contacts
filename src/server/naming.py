import re

VCARD_SUFFIX = ".vcf"

# A resource name becomes a filename in the address book repository, so it has to be safe as one.  This is
# stricter than the DAV spec requires: no separators, no control characters, and no leading dot, which keeps
# ".git" and friends unreachable however a client spells the request.
_SAFE_RESOURCE_NAME = re.compile(r"^[^.\x00-\x1f/\\][^\x00-\x1f/\\]{0,199}$")
# Filesystems cap a name at 255 bytes, not characters, so a name of multi-byte characters can pass the regex and
# then fail in open() as a 500.  Exactly the filesystem's limit, so no file already on disk stops being a contact.
_MAX_RESOURCE_NAME_BYTES = 255 - len(VCARD_SUFFIX)


def is_safe_resource_name(name: str) -> bool:
    if not _SAFE_RESOURCE_NAME.match(name):
        return False
    try:
        return len(name.encode("utf-8")) <= _MAX_RESOURCE_NAME_BYTES
    except UnicodeEncodeError:
        # A lone surrogate cannot become a filename at all.
        return False


def filename_for(resource_name: str) -> str:
    if not is_safe_resource_name(resource_name):
        raise ValueError(f"unsafe resource name: {resource_name!r}")
    return resource_name + VCARD_SUFFIX


def resource_name_from_filename(filename: str) -> str | None:
    if not filename.endswith(VCARD_SUFFIX):
        return None
    resource_name = filename[: -len(VCARD_SUFFIX)]
    return resource_name if is_safe_resource_name(resource_name) else None
