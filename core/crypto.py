import base64
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _build_cipher() -> Fernet:
    key = settings.MASTER_ENCRYPTION_KEY
    if len(key) < 32:
        key = (key * 32)[:32]
    key_bytes = base64.urlsafe_b64encode(key.encode("utf-8")[:32])
    return Fernet(key_bytes)


def encrypt_value(plain: str | None) -> str | None:
    if not plain:
        return None
    try:
        return _build_cipher().encrypt(plain.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


def decrypt_value(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _build_cipher().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, Exception):
        return None
