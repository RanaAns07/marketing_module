import base64
import hashlib
from cryptography.fernet import Fernet
from django.conf import settings

_ENCRYPTED_PREFIX = "enc::v1::"


def _get_fernet() -> Fernet:
    raw_key = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or ""
    if raw_key:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception:
            digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(digest))
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_value(value: str) -> str:
    if value is None or value == "":
        return value
    if value.startswith(_ENCRYPTED_PREFIX):
        return value
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{_ENCRYPTED_PREFIX}{token}"


def decrypt_value(value: str) -> str:
    if value is None or value == "":
        return value
    if not value.startswith(_ENCRYPTED_PREFIX):
        return value
    token = value[len(_ENCRYPTED_PREFIX):]
    return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
