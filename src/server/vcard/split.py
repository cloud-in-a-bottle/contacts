import re

# Matches one line plus whatever terminator it ends with, so the slices below keep the original bytes.
_LINE = re.compile(r"[^\r\n]*(?:\r\n|[\r\n])?")

_BEGIN = "BEGIN:VCARD"
_END = "END:VCARD"

UTF8_BOM = "﻿"


def split_cards(text: str) -> tuple[str, ...]:
    """Split a ``.vcf`` file into its individual vCards, each sliced out of ``text`` unchanged.

    Exports from Google Contacts, iCloud and Thunderbird are all just vCards concatenated, sometimes with blank
    lines between them and sometimes with a byte order mark on the front.  Slicing rather than re-serialising is
    what lets an imported card keep the exact bytes it arrived with.
    """
    cards: list[str] = []
    start: int | None = None
    offset = 0
    for match in _LINE.finditer(text):
        line = match.group(0)
        if not line:
            break
        keyword = line.strip().lstrip(UTF8_BOM).upper()
        if keyword == _BEGIN:
            # An unterminated card before this one is abandoned rather than merged into its successor.
            start = offset
        elif keyword == _END and start is not None:
            cards.append(text[start : offset + len(line)])
            start = None
        offset += len(line)
    return tuple(cards)
