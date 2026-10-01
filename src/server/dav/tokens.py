_SYNC_TOKEN_PREFIX = "urn:cloud-in-a-bottle:contacts:git:"


def sync_token(commit: str) -> str:
    return f"{_SYNC_TOKEN_PREFIX}{commit}"


def parse_sync_token(token: str) -> str | None:
    """Read a sync token back into the commit it names.

    Returns "" for an absent token, which RFC 6578 defines as "send me everything", and None for anything we did
    not issue — the caller answers that with a 409 and a DAV:valid-sync-token precondition.
    """
    candidate = token.strip()
    if not candidate:
        return ""
    if not candidate.startswith(_SYNC_TOKEN_PREFIX):
        return None
    commit = candidate[len(_SYNC_TOKEN_PREFIX) :]
    return commit or None
