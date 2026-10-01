from collections.abc import Sequence
from datetime import UTC
from datetime import datetime

from server.models import Contact

# Litestar appends "; charset=utf-8" to text/* media types itself, so naming the charset here too
# would emit it twice.
EXPORT_CONTENT_TYPE = "text/vcard"


def export_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    return f"contacts-{stamp}.vcf"


def build_export(contacts: Sequence[Contact]) -> bytes:
    """Concatenate every stored vCard into one file.

    The cards go out exactly as they are stored, so an export is a faithful copy of what the clients wrote —
    a card is only given a trailing CRLF if it is missing one, so that the next BEGIN:VCARD starts its own line.
    """
    parts: list[str] = []
    for contact in contacts:
        text = contact.vcard
        if not text.endswith(("\r\n", "\n")):
            text += "\r\n"
        parts.append(text)
    return "".join(parts).encode("utf-8")
