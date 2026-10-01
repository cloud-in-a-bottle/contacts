import quopri

import attr

_FOLD_LIMIT_OCTETS = 75


@attr.s(auto_attribs=True, frozen=True)
class Param:
    name: str
    values: tuple[str, ...]


@attr.s(auto_attribs=True, frozen=True)
class ContentLine:
    """One unfolded vCard content line, kept close to the bytes that arrived.

    ``value`` is still escaped and still encoded (quoted-printable, base64, ...) exactly as the producer wrote it, so
    a line we do not understand can be written back out untouched.  Use the accessors below to read it.
    """

    name: str
    value: str
    params: tuple[Param, ...] = ()
    group: str | None = None
    raw_name: str = ""
    """The property name exactly as it was written.  Names are case-insensitive, but clients such as Apple
    Contacts write mixed-case extensions (``X-ABShowAs``) and get them back unchanged this way."""

    def param(self, name: str) -> tuple[str, ...]:
        """Every value recorded under this parameter name.

        Parameters repeat in the wild — ``EMAIL;type=INTERNET;type=WORK`` and the vCard 2.1 shorthand
        ``TEL;HOME;VOICE`` both produce several TYPE entries — so all of them are gathered, not just the first.
        """
        wanted = name.upper()
        return tuple(value for param in self.params if param.name.upper() == wanted for value in param.values)

    @property
    def types(self) -> tuple[str, ...]:
        """The TYPE values, including the vCard 2.1 shorthand where the type is a bare parameter (``;HOME;VOICE``)."""
        return tuple(value.upper() for value in self.param("TYPE"))


def _split_outside_quotes(text: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for character in text:
        if character == '"':
            in_quotes = not in_quotes
            current.append(character)
        elif character == delimiter and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    parts.append("".join(current))
    return parts


def _is_quoted_printable(header: str) -> bool:
    return "QUOTED-PRINTABLE" in header.upper()


# A folded header longer than this is not a real vCard, and continuing to re-scan for the name/value colon
# would make unfolding quadratic again.  Past it, assume the header is settled and not quoted-printable.
_MAX_FOLDED_HEADER = 1024


def unfold(text: str) -> list[str]:
    """Join continuation lines into whole content lines.

    Handles both wrapping styles found in the wild: RFC 6350 folding (a continuation starts with a space or tab) and
    the vCard 2.1 quoted-printable soft line break (the line ends with ``=``).

    Chunks are collected in a list and joined once per logical line.  Appending to a string instead makes this
    quadratic, which matters more than it sounds: an inline PHOTO is a single value folded over thousands of
    lines, and re-copying the accumulated value for each of them dominated the cost of reading an address book.
    """
    lines: list[str] = []
    chunks: list[str] = []
    header: str | None = None
    is_quoted_printable = False

    def flush() -> None:
        if chunks:
            joined = "".join(chunks)
            if joined.strip():
                lines.append(joined)
            chunks.clear()

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if chunks and raw_line[:1] in (" ", "\t"):
            chunks.append(raw_line[1:])
        elif chunks and is_quoted_printable and chunks[-1].endswith("="):
            chunks[-1] = chunks[-1][:-1]
            chunks.append(raw_line)
        else:
            flush()
            chunks.append(raw_line)
            name, separator, _ = raw_line.partition(":")
            header = name if separator else None
            is_quoted_printable = bool(separator) and _is_quoted_printable(name)
            continue

        if header is None:
            # The colon had not arrived yet when this line started, so the header is itself folded.  Keep
            # looking for it, but only while the accumulated text is short enough for that to be cheap.
            joined = "".join(chunks)
            name, separator, _ = joined.partition(":")
            if separator:
                header = name
                is_quoted_printable = _is_quoted_printable(name)
            elif len(joined) > _MAX_FOLDED_HEADER:
                header = ""

    flush()
    return lines


def parse_line(line: str) -> ContentLine | None:
    """Parse one unfolded content line, returning None for anything that is not ``name[;params]:value``."""
    header, separator, value = "", "", ""
    in_quotes = False
    for index, character in enumerate(line):
        if character == '"':
            in_quotes = not in_quotes
        elif character == ":" and not in_quotes:
            header, separator, value = line[:index], ":", line[index + 1 :]
            break
    if not separator:
        return None

    segments = _split_outside_quotes(header, ";")
    name_segment = segments[0]
    group: str | None = None
    if "." in name_segment:
        group, _, name_segment = name_segment.partition(".")
    name = name_segment.strip().upper()
    if not name:
        return None

    params: list[Param] = []
    for segment in segments[1:]:
        if not segment.strip():
            continue
        key, equals, raw_values = segment.partition("=")
        if not equals:
            # vCard 2.1 shorthand: a bare parameter value is a TYPE.
            params.append(Param(name="TYPE", values=(_unquote_param(segment.strip()),)))
            continue
        values = tuple(_unquote_param(part.strip()) for part in _split_outside_quotes(raw_values, ","))
        params.append(Param(name=key.strip().upper(), values=values))

    return ContentLine(name=name, value=value, params=tuple(params), group=group, raw_name=name_segment.strip())


def _unquote_param(value: str) -> str:
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _quote_param(value: str) -> str:
    if any(character in value for character in ',;:"'):
        return '"' + value.replace('"', "") + '"'
    return value


def decode_value(line: ContentLine) -> str:
    """Undo any transfer encoding on the line's value, leaving vCard escaping in place."""
    encodings = {value.upper() for value in line.param("ENCODING")}
    if "QUOTED-PRINTABLE" not in encodings:
        return line.value
    charset = (line.param("CHARSET") or ("UTF-8",))[0]
    try:
        return quopri.decodestring(line.value.encode("utf-8", errors="replace")).decode(charset, errors="replace")
    except LookupError:
        return quopri.decodestring(line.value.encode("utf-8", errors="replace")).decode("utf-8", errors="replace")


def unescape_text(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            following = value[index + 1]
            if following in ("n", "N"):
                result.append("\n")
            else:
                result.append(following)
            index += 2
            continue
        result.append(character)
        index += 1
    return "".join(result)


def escape_text(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return escaped.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def split_escaped(value: str, delimiter: str) -> list[str]:
    """Split on an unescaped delimiter, leaving the escaping of each part intact."""
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            current.append(value[index : index + 2])
            index += 2
            continue
        if character == delimiter:
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(character)
        index += 1
    parts.append("".join(current))
    return parts


def text_value(line: ContentLine) -> str:
    return unescape_text(decode_value(line))


def structured_value(line: ContentLine) -> tuple[tuple[str, ...], ...]:
    """Split a structured value (N, ADR, ORG) into its ``;``-separated fields, each a tuple of ``,`` components."""
    decoded = decode_value(line)
    return tuple(
        tuple(unescape_text(component) for component in split_escaped(field, ","))
        for field in split_escaped(decoded, ";")
    )


def render_line(line: ContentLine) -> str:
    written_name = line.raw_name or line.name
    name = f"{line.group}.{written_name}" if line.group else written_name
    segments = [name]
    for param in line.params:
        rendered_values = ",".join(_quote_param(value) for value in param.values)
        segments.append(f"{param.name}={rendered_values}" if rendered_values else param.name)
    return ";".join(segments) + ":" + line.value


def fold(line: str) -> list[str]:
    """Wrap a rendered content line to 75 octets, never splitting a UTF-8 code point."""
    if len(line.encode("utf-8")) <= _FOLD_LIMIT_OCTETS:
        return [line]
    chunks: list[str] = []
    current: list[str] = []
    current_octets = 0
    limit = _FOLD_LIMIT_OCTETS
    for character in line:
        width = len(character.encode("utf-8"))
        if current_octets + width > limit:
            chunks.append("".join(current))
            current = []
            current_octets = 0
            # Continuation lines carry a leading space that counts against the limit.
            limit = _FOLD_LIMIT_OCTETS - 1
        current.append(character)
        current_octets += width
    if current:
        chunks.append("".join(current))
    return [chunks[0]] + [" " + chunk for chunk in chunks[1:]]
