from server.vcard.build import render_vcard
from server.vcard.lines import fold
from server.vcard.lines import parse_line
from server.vcard.lines import render_line
from server.vcard.lines import unfold
from server.vcard.model import ContactFields
from server.vcard.model import PostalAddress
from server.vcard.model import StructuredName
from server.vcard.model import TypedValue
from server.vcard.parse import extract_fields
from server.vcard.parse import extract_photo
from server.vcard.parse import parse_vcard
from server.vcard.summary import summarize

APPLE_CARD = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "N:Okonkwo;Amara;Ngozi;Dr.;PhD\r\n"
    "FN:Dr. Amara Okonkwo\r\n"
    "ORG:Riverbank Press;Editorial\r\n"
    "TITLE:Commissioning Editor\r\n"
    "EMAIL;type=INTERNET;type=WORK;type=pref:amara@riverbank.example\r\n"
    "EMAIL;type=INTERNET;type=HOME:amara@home.example\r\n"
    "TEL;type=CELL;type=VOICE:+44 7700 900123\r\n"
    "ADR;type=WORK:;;12 Wharf Road\\nUnit 4;Bristol;;BS1 4QD;United Kingdom\r\n"
    "NOTE:Prefers email\\, not phone\\; mornings only.\r\n"
    "CATEGORIES:work,publishing\r\n"
    "X-ABShowAs:COMPANY\r\n"
    "UID:11111111-2222-3333-4444-555555555555\r\n"
    "END:VCARD\r\n"
)


def test_unfold_joins_continuation_lines() -> None:
    folded = "NOTE:this is a long\r\n  note that wraps\r\nFN:Someone\r\n"
    assert unfold(folded) == ["NOTE:this is a long note that wraps", "FN:Someone"]


def test_unfold_joins_quoted_printable_soft_breaks() -> None:
    raw = "NOTE;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:caf=C3=\r\n=A9 time\r\n"
    assert unfold(raw) == ["NOTE;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:caf=C3=A9 time"]


def test_quoted_printable_values_are_decoded() -> None:
    card = parse_vcard("BEGIN:VCARD\r\nNOTE;ENCODING=QUOTED-PRINTABLE;CHARSET=UTF-8:caf=C3=A9\r\nEND:VCARD\r\n")
    assert extract_fields(card).note == "café"


def test_parse_line_keeps_colons_inside_quoted_parameters() -> None:
    line = parse_line('URL;TYPE="a:b":https://example.com/x')
    assert line is not None
    assert line.name == "URL"
    assert line.param("TYPE") == ("a:b",)
    assert line.value == "https://example.com/x"


def test_bare_parameters_are_read_as_types() -> None:
    line = parse_line("TEL;HOME;VOICE:555")
    assert line is not None
    assert line.types == ("HOME", "VOICE")


def test_extract_fields_reads_a_realistic_card() -> None:
    fields = extract_fields(parse_vcard(APPLE_CARD))
    assert fields.formatted_name == "Dr. Amara Okonkwo"
    assert fields.name == StructuredName(
        family="Okonkwo", given="Amara", additional="Ngozi", prefix="Dr.", suffix="PhD"
    )
    assert fields.organization == "Riverbank Press"
    assert fields.department == "Editorial"
    assert fields.job_title == "Commissioning Editor"
    assert [email.value for email in fields.emails] == ["amara@riverbank.example", "amara@home.example"]
    assert fields.emails[0].label == "work"
    assert fields.phones[0].label == "mobile/voice"
    assert fields.addresses[0].city == "Bristol"
    assert fields.addresses[0].postal_code == "BS1 4QD"
    assert fields.note == "Prefers email, not phone; mornings only."
    assert fields.categories == ("work", "publishing")


def test_render_round_trips_through_the_parser() -> None:
    fields = ContactFields(
        formatted_name="Björn Ålesund",
        name=StructuredName(family="Ålesund", given="Björn"),
        organization="Nordlys AS",
        emails=(TypedValue(value="bjorn@nordlys.example", label="work"),),
        phones=(TypedValue(value="+47 22 00 00 00", label="mobile"),),
        addresses=(PostalAddress(street="Storgata 1", city="Oslo", postal_code="0155", country="Norway"),),
        note="Semi-colons; commas, and\nnewlines all survive.",
        categories=("supplier",),
    )
    text = render_vcard(fields, uid="abc-123")
    reparsed = extract_fields(parse_vcard(text))

    assert reparsed.formatted_name == fields.formatted_name
    assert reparsed.name.family == "Ålesund"
    assert reparsed.organization == "Nordlys AS"
    assert reparsed.emails[0].value == "bjorn@nordlys.example"
    assert reparsed.emails[0].label == "work"
    assert reparsed.phones[0].label == "mobile"
    assert reparsed.addresses[0].city == "Oslo"
    assert reparsed.note == fields.note
    assert reparsed.categories == ("supplier",)


def test_render_preserves_properties_the_editor_does_not_own() -> None:
    fields = extract_fields(parse_vcard(APPLE_CARD))
    updated = render_vcard(fields, uid="11111111-2222-3333-4444-555555555555", existing_text=APPLE_CARD)
    assert "X-ABShowAs:COMPANY" in updated
    # ...and does not duplicate the ones it does own.
    assert updated.count("\r\nFN:") == 1
    assert updated.count("\r\nUID:") == 1


def test_render_never_emits_an_overlong_line() -> None:
    fields = ContactFields(formatted_name="x" * 400, note="ü" * 200)
    for line in render_vcard(fields, uid="u").split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_fold_does_not_split_a_code_point() -> None:
    folded = fold("NOTE:" + "é" * 60)
    assert "".join([folded[0]] + [part[1:] for part in folded[1:]]) == "NOTE:" + "é" * 60


def test_render_line_requotes_parameters_that_need_it() -> None:
    line = parse_line('X-THING;NOTE="a;b":value')
    assert line is not None
    assert render_line(line) == 'X-THING;NOTE="a;b":value'


def test_extract_photo_decodes_base64_and_data_uris() -> None:
    pixel = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    v3 = parse_vcard(f"BEGIN:VCARD\r\nPHOTO;ENCODING=b;TYPE=PNG:{pixel}\r\nEND:VCARD\r\n")
    photo = extract_photo(v3)
    assert photo is not None
    assert photo.media_type == "image/png"
    assert photo.data.startswith(b"\x89PNG")

    v4 = parse_vcard(f"BEGIN:VCARD\r\nPHOTO:data:image/png;base64,{pixel}\r\nEND:VCARD\r\n")
    assert extract_photo(v4) is not None

    linked = parse_vcard("BEGIN:VCARD\r\nPHOTO;VALUE=URI:https://example.com/a.png\r\nEND:VCARD\r\n")
    assert extract_photo(linked) is None


def test_summarize_derives_index_columns() -> None:
    summary = summarize(APPLE_CARD, fallback_uid="fallback")
    assert summary.uid == "11111111-2222-3333-4444-555555555555"
    assert summary.display_name == "Dr. Amara Okonkwo"
    assert summary.sort_key == "okonkwo amara"
    assert "riverbank" in summary.search_text
    assert "447700900123" in summary.search_text


def test_summarize_falls_back_to_the_resource_name_without_a_uid() -> None:
    summary = summarize("BEGIN:VCARD\r\nFN:No Uid\r\nEND:VCARD\r\n", fallback_uid="res-1")
    assert summary.uid == "res-1"
    assert summary.display_name == "No Uid"
