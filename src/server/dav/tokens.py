_SYNC_TOKEN_PREFIX = "urn:cloud-in-a-bottle:contacts:sync:"


def sync_token(change_seq: int) -> str:
    return f"{_SYNC_TOKEN_PREFIX}{change_seq}"


def parse_sync_token(token: str) -> int | None:
    """Read a sync token back into a change sequence, or None if it is not one we issued."""
    candidate = token.strip()
    if not candidate:
        # An absent or empty sync-token means "send me everything" per RFC 6578.
        return 0
    if not candidate.startswith(_SYNC_TOKEN_PREFIX):
        return None
    suffix = candidate[len(_SYNC_TOKEN_PREFIX) :]
    if not suffix.isdigit():
        return None
    return int(suffix)
