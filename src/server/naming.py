import re

VCARD_SUFFIX = ".vcf"

# A resource name becomes a filename in the address book repository, so it has to be safe as one.  This is
# stricter than the DAV spec requires: no separators, no control characters, and no leading dot, which keeps
# ".git" and friends unreachable however a client spells the request.
_SAFE_RESOURCE_NAME = re.compile(r"^[^.\x00-\x1f/\\][^\x00-\x1f/\\]{0,199}$")


def is_safe_resource_name(name: str) -> bool:
    return bool(_SAFE_RESOURCE_NAME.match(name))


def filename_for(resource_name: str) -> str:
    if not is_safe_resource_name(resource_name):
        raise ValueError(f"unsafe resource name: {resource_name!r}")
    return resource_name + VCARD_SUFFIX


def resource_name_from_filename(filename: str) -> str | None:
    if not filename.endswith(VCARD_SUFFIX):
        return None
    resource_name = filename[: -len(VCARD_SUFFIX)]
    return resource_name if is_safe_resource_name(resource_name) else None
