from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlsplit


ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
ALLOWED_INTERVALS = {1, 5, 15, 30, 60}
HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
FORBIDDEN_HEADERS = {
    "connection",
    "content-length",
    "host",
    "proxy-authorization",
    "transfer-encoding",
}


def validate_endpoint_url(value: str) -> str:
    url = value.strip()
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Endpoint must use http:// or https://.")
    if not parsed.hostname:
        raise ValueError("Endpoint must include a hostname.")
    if parsed.username or parsed.password:
        raise ValueError("Embedded URL credentials are not allowed.")
    return url


def validate_public_endpoint(value: str) -> str:
    url = validate_endpoint_url(value)
    hostname = urlsplit(url).hostname
    try:
        address = ipaddress.ip_address(hostname or "")
    except ValueError:
        return url
    if not address.is_global:
        raise ValueError("Private, loopback, and link-local IP endpoints are not allowed.")
    return url


def parse_json_object(value: str, field_name: str) -> dict[str, str]:
    if not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    headers = {str(key): str(item) for key, item in parsed.items()}
    for name in headers:
        validate_header_name(name, field_name)
    return headers


def validate_header_name(value: str, field_name: str = "Header") -> str:
    name = value.strip()
    if not name or not HEADER_NAME.fullmatch(name):
        raise ValueError(f"{field_name} contains an invalid HTTP header name.")
    if name.lower() in FORBIDDEN_HEADERS:
        raise ValueError(f"{field_name} cannot set the {name} header.")
    return name


def validate_json_body(value: str) -> str | None:
    if not value.strip():
        return None
    try:
        json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    return value.strip()
