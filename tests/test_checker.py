from __future__ import annotations

import httpx
import pytest

from app.checker import (
    inferred_capabilities,
    metadata_capabilities,
    model_endpoint_candidates,
    probe_account,
    provider_headers,
)
from app.models import Account
from app.security import encrypt_secret


def account(**values) -> Account:
    defaults = {
        "name": "Example",
        "endpoint_url": "https://example.com",
        "encrypted_api_key": encrypt_secret("sk-secret"),
        "timeout_seconds": 5,
        "interval_minutes": 5,
        "enabled": True,
    }
    defaults.update(values)
    return Account(**defaults)


def mock_client(monkeypatch, handler):
    real_client = httpx.AsyncClient

    class Client:
        async def __aenter__(self):
            self.client = real_client(transport=httpx.MockTransport(handler))
            return self.client

        async def __aexit__(self, *args):
            await self.client.aclose()

    monkeypatch.setattr("app.checker.httpx.AsyncClient", lambda **kwargs: Client())


def test_model_endpoint_candidates_normalize_v1():
    assert model_endpoint_candidates("https://api.example.com") == [
        "https://api.example.com/v1/models",
        "https://api.example.com/models",
    ]
    assert model_endpoint_candidates("https://api.example.com/v1") == [
        "https://api.example.com/v1/models",
        "https://api.example.com/models",
    ]
    assert model_endpoint_candidates("https://api.example.com/v1/models") == [
        "https://api.example.com/v1/models"
    ]


def test_provider_headers_allow_optional_key():
    assert provider_headers("openai", "") == {"Accept": "application/json"}
    anthropic = provider_headers("anthropic", "token")
    assert anthropic["x-api-key"] == "token"
    assert anthropic["anthropic-version"]


@pytest.mark.asyncio
async def test_detect_openai_models(monkeypatch):
    async def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer sk-secret"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-beta", "object": "model", "owned_by": "provider"},
                    {"id": "gpt-alpha", "object": "model", "owned_by": "provider"},
                ],
            },
        )

    mock_client(monkeypatch, handler)
    result = await probe_account(account())
    assert result.status == "healthy"
    assert result.provider_type == "openai"
    assert [model["id"] for model in result.models] == ["gpt-alpha", "gpt-beta"]
    assert result.models_endpoint == "https://example.com/v1/models"


@pytest.mark.asyncio
async def test_detect_anthropic_models(monkeypatch):
    async def handler(request):
        if "x-api-key" not in request.headers:
            return httpx.Response(
                401,
                json={
                    "type": "error",
                    "error": {"type": "authentication_error"},
                },
            )
        assert request.headers["x-api-key"] == "sk-secret"
        assert request.headers["anthropic-version"]
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "claude-sonnet",
                        "type": "model",
                        "display_name": "Claude Sonnet",
                        "created_at": "2025-01-01T00:00:00Z",
                    }
                ],
                "first_id": "claude-sonnet",
                "last_id": "claude-sonnet",
            },
        )

    mock_client(monkeypatch, handler)
    result = await probe_account(account())
    assert result.status == "healthy"
    assert result.provider_type == "anthropic"
    assert [model["id"] for model in result.models] == ["claude-sonnet"]


@pytest.mark.asyncio
async def test_public_openai_endpoint_without_key(monkeypatch):
    async def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"id": "public-model", "object": "model"}],
            },
        )

    mock_client(monkeypatch, handler)
    result = await probe_account(
        account(encrypted_api_key=encrypt_secret(""))
    )
    assert result.status == "healthy"
    assert [model["id"] for model in result.models] == ["public-model"]


@pytest.mark.asyncio
async def test_probe_strips_whitespace_from_stored_key(monkeypatch):
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer clean-key"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"id": "model", "object": "model"}],
            },
        )

    mock_client(monkeypatch, handler)
    result = await probe_account(
        account(encrypted_api_key=encrypt_secret(" clean-key \n"))
    )
    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_authentication_failure_identifies_provider(monkeypatch):
    async def handler(request):
        return httpx.Response(
            401,
            json={"error": {"message": "invalid key", "type": "invalid_request_error"}},
        )

    mock_client(monkeypatch, handler)
    result = await probe_account(account())
    assert result.status == "down"
    assert result.provider_type == "openai"
    assert result.http_status == 401
    assert "API key" in result.error_message


def test_capabilities_distinguish_provider_metadata_and_inference():
    assert inferred_capabilities("gpt-4o-mini")["vision"] == "inferred"
    assert inferred_capabilities("deepseek-r1")["reasoning"] == "inferred"
    assert metadata_capabilities(
        {"capabilities": {"vision": True, "function_calling": True}}
    ) == {"vision": "provider", "tools": "provider"}
