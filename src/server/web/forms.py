from collections.abc import Sequence

from litestar.datastructures import FormMultiDict

from server.vcard.model import ContactFields
from server.vcard.model import PostalAddress
from server.vcard.model import StructuredName
from server.vcard.model import TypedValue


def _values(form: FormMultiDict, key: str) -> list[str]:
    return [value for value in form.getall(key, []) if isinstance(value, str)]


def _single(form: FormMultiDict, key: str) -> str:
    value = form.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _typed_values(form: FormMultiDict, prefix: str) -> tuple[TypedValue, ...]:
    values = _values(form, f"{prefix}_value")
    labels = _values(form, f"{prefix}_label")
    return tuple(
        TypedValue(value=value.strip(), label=_label_at(labels, index))
        for index, value in enumerate(values)
        if value.strip()
    )


def _label_at(labels: Sequence[str], index: int) -> str:
    return labels[index].strip() if index < len(labels) else ""


def _addresses(form: FormMultiDict) -> tuple[PostalAddress, ...]:
    streets = _values(form, "address_street")
    addresses = [
        PostalAddress(
            street=street.strip(),
            city=_label_at(_values(form, "address_city"), index),
            region=_label_at(_values(form, "address_region"), index),
            postal_code=_label_at(_values(form, "address_postal_code"), index),
            country=_label_at(_values(form, "address_country"), index),
            label=_label_at(_values(form, "address_label"), index),
        )
        for index, street in enumerate(streets)
    ]
    return tuple(address for address in addresses if not address.is_empty)


def contact_fields_from_form(form: FormMultiDict) -> ContactFields:
    """Read the edit form back into the structured fields the vCard writer understands."""
    return ContactFields(
        formatted_name=_single(form, "formatted_name"),
        name=StructuredName(
            family=_single(form, "family"),
            given=_single(form, "given"),
            additional=_single(form, "additional"),
            prefix=_single(form, "prefix"),
            suffix=_single(form, "suffix"),
        ),
        nickname=_single(form, "nickname"),
        organization=_single(form, "organization"),
        department=_single(form, "department"),
        job_title=_single(form, "job_title"),
        emails=_typed_values(form, "email"),
        phones=_typed_values(form, "phone"),
        addresses=_addresses(form),
        urls=_typed_values(form, "url"),
        birthday=_single(form, "birthday"),
        note=_single(form, "note"),
        categories=tuple(part.strip() for part in _single(form, "categories").split(",") if part.strip()),
    )
