from app.security import decrypt_secret, encrypt_secret, redact_secret


def test_secret_round_trip():
    encrypted = encrypt_secret("sk-private-value")
    assert encrypted != "sk-private-value"
    assert decrypt_secret(encrypted) == "sk-private-value"


def test_redact_secret_and_limit_message():
    value = "prefix sk-secret suffix" + ("x" * 600)
    redacted = redact_secret(value, "sk-secret")
    assert "sk-secret" not in redacted
    assert "[REDACTED]" in redacted
    assert len(redacted) == 500
