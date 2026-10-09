# apps/accounts/crypto.py
"""Reversible encryption for default passwords (the key lives only in .env)."""
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def encrypt_secret(plain: str) -> str:
    return Fernet(settings.DEFAULT_PASSWORD_KEY).encrypt(plain.encode()).decode()


def decrypt_secret(token: str):
    """Plain text, or None if the token is empty or the key changed."""
    try:
        return Fernet(settings.DEFAULT_PASSWORD_KEY).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, AttributeError):
        return None