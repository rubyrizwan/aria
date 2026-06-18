from app.main import account_form_values, verification_candidate
from app.security import decrypt_secret, encrypt_secret


def clean_values(**overrides):
    values = {
        "name": "Provider",
        "endpoint_url": "https://example.com",
        "api_key": "",
        "remove_api_key": False,
        "timeout_seconds": 10,
        "interval_minutes": 5,
        "enabled": True,
    }
    values.update(overrides)
    return values


def test_verification_uses_entered_api_key():
    candidate = verification_candidate(clean_values(api_key="new-key"))
    assert decrypt_secret(candidate.encrypted_api_key) == "new-key"


def test_verification_reuses_or_removes_existing_api_key():
    existing = encrypt_secret("stored-key")
    reused = verification_candidate(clean_values(), existing)
    removed = verification_candidate(clean_values(remove_api_key=True), existing)

    assert decrypt_secret(reused.encrypted_api_key) == "stored-key"
    assert decrypt_secret(removed.encrypted_api_key) == ""


def test_api_key_input_strips_surrounding_whitespace():
    values = account_form_values(
        {
            "name": "Provider",
            "endpoint_url": "https://example.com",
            "api_key": "  key-with-whitespace \r\n",
        }
    )
    assert values["api_key"] == "key-with-whitespace"
