import pytest

from app.validation import (
    parse_json_object,
    validate_endpoint_url,
    validate_header_name,
    validate_json_body,
)


def test_validate_endpoint_url():
    assert validate_endpoint_url("https://example.com/health") == "https://example.com/health"


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com", "https://user:pass@example.com", "not-a-url"],
)
def test_reject_invalid_endpoint_url(url):
    with pytest.raises(ValueError):
        validate_endpoint_url(url)


def test_json_fields():
    assert parse_json_object('{"X-Team": 12}', "Headers") == {"X-Team": "12"}
    assert validate_json_body('{"ok": true}') == '{"ok": true}'


def test_reject_json_array_headers():
    with pytest.raises(ValueError):
        parse_json_object("[]", "Headers")


@pytest.mark.parametrize("header", ["Host", "Content-Length", "bad header"])
def test_reject_unsafe_or_invalid_headers(header):
    with pytest.raises(ValueError):
        validate_header_name(header)
