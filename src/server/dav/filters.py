from xml.etree import ElementTree

from server.dav.xml import CARDDAV_NS
from server.vcard.lines import text_value
from server.vcard.parse import parse_vcard

_ANY_OF = "anyof"


def _test_mode(node: ElementTree.Element) -> str:
    return (node.get("test") or _ANY_OF).strip().lower()


def _combine(results: list[bool], test: str) -> bool:
    if not results:
        return True
    return all(results) if test == "allof" else any(results)


def _text_matches(haystack: str, needle: str, match_type: str) -> bool:
    if match_type == "equals":
        return haystack == needle
    if match_type == "starts-with":
        return haystack.startswith(needle)
    if match_type == "ends-with":
        return haystack.endswith(needle)
    return needle in haystack


def _matches_text_match(node: ElementTree.Element, values: list[str]) -> bool:
    needle = (node.text or "").lower()
    match_type = (node.get("match-type") or "contains").strip().lower()
    matched = any(_text_matches(value.lower(), needle, match_type) for value in values)
    if (node.get("negate-condition") or "no").strip().lower() == "yes":
        return not matched
    return matched


def _matches_prop_filter(node: ElementTree.Element, card_values: dict[str, list[str]]) -> bool:
    property_name = (node.get("name") or "").upper()
    values = card_values.get(property_name, [])
    conditions: list[bool] = []
    for child in node:
        if child.tag == f"{{{CARDDAV_NS}}}is-not-defined":
            conditions.append(not values)
        elif child.tag == f"{{{CARDDAV_NS}}}text-match":
            conditions.append(_matches_text_match(child, values))
        # param-filter and anything else we do not model are treated as matching, so an unsupported filter
        # over-returns rather than silently hiding contacts from a client.
    if not conditions:
        return bool(values)
    return _combine(conditions, _test_mode(node))


def matches_filter(filter_node: ElementTree.Element | None, vcard_text: str) -> bool:
    """Evaluate a CardDAV ``C:filter`` against a vCard.

    Only ``prop-filter`` with ``is-not-defined`` and ``text-match`` is modelled.  Anything richer is treated as a
    match: returning a contact the client then discards is harmless, whereas dropping one loses data from its view.
    """
    if filter_node is None:
        return True
    prop_filters = filter_node.findall(f"{{{CARDDAV_NS}}}prop-filter")
    if not prop_filters:
        return True
    card_values: dict[str, list[str]] = {}
    for line in parse_vcard(vcard_text).lines:
        card_values.setdefault(line.name, []).append(text_value(line))
    return _combine([_matches_prop_filter(node, card_values) for node in prop_filters], _test_mode(filter_node))
