from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class SecretConfigurationError(RuntimeError):
    pass


def get_fernet() -> Fernet:
    if not settings.master_key:
        raise SecretConfigurationError(
            "APICHECKER_MASTER_KEY is required. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"`."
        )
    try:
        return Fernet(settings.master_key.encode())
    except (ValueError, TypeError) as exc:
        raise SecretConfigurationError("APICHECKER_MASTER_KEY is not a valid Fernet key.") from exc


def encrypt_secret(value: str) -> str:
    return get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    try:
        return get_fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise SecretConfigurationError(
            "Unable to decrypt API key with the configured master key."
        ) from exc


def redact_secret(message: str | None, secret: str | None) -> str | None:
    if not message:
        return message
    redacted = message
    if secret:
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted[:500]
