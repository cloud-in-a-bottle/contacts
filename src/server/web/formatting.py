from datetime import datetime


def humanize_timestamp(iso_timestamp: str) -> str:
    """Render a stored ISO-8601 timestamp for display, falling back to the raw string if it will not parse."""
    try:
        parsed = datetime.fromisoformat(iso_timestamp)
    except ValueError:
        return iso_timestamp
    return parsed.strftime("%-d %b %Y, %H:%M UTC")
