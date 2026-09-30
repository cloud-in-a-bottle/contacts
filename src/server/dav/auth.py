import base64
import binascii

from server.credentials import CredentialStore

BASIC_PREFIX = "basic "


def is_authorized(authorization_header: str, credentials: CredentialStore) -> bool:
    """Check an HTTP Basic credential against the CardDAV password.

    The username is ignored: this app serves exactly one address book and clients vary in what they insist on
    putting there, so the password is the whole secret.
    """
    if not authorization_header.lower().startswith(BASIC_PREFIX):
        return False
    encoded = authorization_header[len(BASIC_PREFIX) :].strip()
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False
    _, separator, password = decoded.partition(":")
    if not separator:
        return False
    return credentials.verify_carddav_password(password)
