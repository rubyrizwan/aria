from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.models import Account
from app.security import decrypt_secret, redact_secret
from app.validation import validate_public_endpoint

ANTHROPIC_VERSION = "2023-06-01"


@dataclass
class ProbeResult:
    status: str
    http_status: int | None
    latency_ms: float | None
    error_message: str | None = None
    provider_type: str = "unknown"
    models: list[dict] = field(default_factory=list)
    models_endpoint: str | None = None


@dataclass
class InferenceResult:
    status: str
    http_status: int | None
    latency_ms: float | None
    error_message: str | None = None


def model_endpoint_candidates(base_url: str) -> list[str]:
    parsed = urlsplit(base_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/models"):
        paths = [path]
    elif path.endswith("/v1"):
        paths = [f"{path}/models", f"{path.removesuffix('/v1')}/models"]
    else:
        paths = [f"{path}/v1/models", f"{path}/models"]

    candidates = []
    for candidate_path in paths:
        candidate = urlunsplit(
            (parsed.scheme, parsed.netloc, candidate_path or "/models", "", "")
        )
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def provider_headers(provider_type: str, api_key: str) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if provider_type == "openai" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider_type == "anthropic":
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if api_key:
            headers["x-api-key"] = api_key
    return headers


def inference_endpoint(base_url: str, provider_type: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    for suffix in ("/models", "/chat/completions", "/messages"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    resource = "messages" if provider_type == "anthropic" else "chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, f"{path}/{resource}", "", ""))


def unsupported_inference_model(model_id: str) -> bool:
    name = model_id.casefold()
    markers = (
        "embedding",
        "embed-",
        "text-embedding",
        "image",
        "dall-e",
        "tts",
        "whisper",
        "transcri",
        "audio",
        "rerank",
        "moderation",
    )
    return any(marker in name for marker in markers)


def response_error_message(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return str(message) if message else None
    if isinstance(error, str):
        return error
    message = payload.get("message")
    return str(message) if message else None


def classify_inference_response(response: httpx.Response) -> tuple[str, str | None]:
    message = response_error_message(response)
    if response.is_success:
        return "available", None
    if response.status_code == 401:
        return "unauthorized", message or "API key ditolak."
    if response.status_code == 403:
        return "forbidden", message or "API key tidak memiliki akses ke model."
    if response.status_code == 404:
        return "unavailable", message or "Model atau endpoint inference tidak tersedia."
    if response.status_code == 429:
        return "quota_exceeded", message or "Rate limit atau kuota provider tercapai."
    return "failed", message or f"Provider merespons HTTP {response.status_code}."


async def test_model_inference(account: Account, model_id: str) -> InferenceResult:
    if unsupported_inference_model(model_id):
        return InferenceResult(
            "unsupported",
            None,
            None,
            "Tipe model ini belum didukung oleh inference test.",
        )
    if account.provider_type not in {"openai", "anthropic"}:
        return InferenceResult(
            "unsupported",
            None,
            None,
            "Compatibility provider belum terdeteksi.",
        )

    secret = decrypt_secret(account.encrypted_api_key).strip()
    started = time.perf_counter()
    try:
        endpoint = validate_public_endpoint(
            inference_endpoint(account.endpoint_url, account.provider_type)
        )
        headers = provider_headers(account.provider_type, secret)
        headers["Content-Type"] = "application/json"
        payload = {
            "model": model_id,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "Hi"}],
        }
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=account.timeout_seconds,
            )
        status, error = classify_inference_response(response)
        return InferenceResult(
            status,
            response.status_code,
            round((time.perf_counter() - started) * 1000, 2),
            redact_secret(error, secret),
        )
    except httpx.TimeoutException:
        return InferenceResult(
            "timeout",
            None,
            round((time.perf_counter() - started) * 1000, 2),
            "Request inference timeout.",
        )
    except (httpx.NetworkError, httpx.InvalidURL, ValueError) as exc:
        return InferenceResult(
            "failed",
            None,
            round((time.perf_counter() - started) * 1000, 2),
            redact_secret(str(exc), secret),
        )


def metadata_capabilities(item: dict) -> dict[str, str]:
    values: list[str] = []
    for key in ("capabilities", "modalities", "input_modalities"):
        value = item.get(key)
        if isinstance(value, list):
            values.extend(str(entry).lower() for entry in value)
        elif isinstance(value, dict):
            values.extend(
                str(name).lower() for name, enabled in value.items() if enabled
            )

    combined = " ".join(values)
    capabilities = {}
    mappings = {
        "vision": ("vision", "image"),
        "reasoning": ("reasoning", "thinking"),
        "audio": ("audio", "speech"),
        "tools": ("tool", "function"),
    }
    for capability, markers in mappings.items():
        if any(marker in combined for marker in markers):
            capabilities[capability] = "provider"
    return capabilities


def inferred_capabilities(model_id: str) -> dict[str, str]:
    name = model_id.lower()
    capabilities = {}
    vision_markers = (
        "vision",
        "gpt-4o",
        "gpt-4.1",
        "gpt-4.5",
        "claude-3",
        "claude-sonnet-4",
        "claude-opus-4",
        "gemini",
        "qwen-vl",
        "llava",
        "pixtral",
    )
    reasoning_markers = (
        "reasoning",
        "thinking",
        "deepseek-r1",
        "qwq",
        "o1",
        "o3",
        "o4",
    )
    if any(marker in name for marker in vision_markers):
        capabilities["vision"] = "inferred"
    if any(marker in name for marker in reasoning_markers):
        capabilities["reasoning"] = "inferred"
    return capabilities


def model_info(item: dict) -> dict:
    model_id = str(item["id"])
    capabilities = inferred_capabilities(model_id)
    capabilities.update(metadata_capabilities(item))
    return {
        "id": model_id,
        "display_name": str(item.get("display_name") or model_id),
        "owned_by": str(item.get("owned_by") or ""),
        "created": item.get("created_at") or item.get("created"),
        "capabilities": capabilities,
    }


def classify_payload(payload: object, attempted_type: str) -> tuple[str, list[dict]]:
    if not isinstance(payload, dict):
        return "unknown", []

    data = payload.get("data")
    if isinstance(data, list):
        models_by_id = {
            model["id"]: model
            for model in (
                model_info(item)
                for item in data
                if isinstance(item, dict) and item.get("id")
            )
        }
        models = sorted(models_by_id.values(), key=lambda model: model["id"].casefold())
        anthropic_markers = any(
            isinstance(item, dict)
            and ("display_name" in item or "created_at" in item)
            for item in data
        )
        if anthropic_markers or "first_id" in payload or "last_id" in payload:
            return "anthropic", models
        if payload.get("object") == "list" or any(
            isinstance(item, dict) and "owned_by" in item for item in data
        ):
            return "openai", models
        return attempted_type, models

    error = payload.get("error")
    if payload.get("type") == "error" and isinstance(error, dict):
        return "anthropic", []
    if isinstance(error, dict):
        return "openai", []
    return "unknown", []


async def probe_account(account: Account) -> ProbeResult:
    secret = decrypt_secret(account.encrypted_api_key).strip()
    started = time.perf_counter()
    failures: list[tuple[str, int | None, str]] = []

    try:
        candidates = [
            validate_public_endpoint(url)
            for url in model_endpoint_candidates(account.endpoint_url)
        ]
        async with httpx.AsyncClient(follow_redirects=False) as client:
            for endpoint in candidates:
                for attempted_type in ("openai", "anthropic"):
                    try:
                        response = await client.get(
                            endpoint,
                            headers=provider_headers(attempted_type, secret),
                            timeout=account.timeout_seconds,
                        )
                    except (httpx.TimeoutException, httpx.NetworkError) as exc:
                        failures.append((attempted_type, None, str(exc)))
                        continue

                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    detected_type, models = classify_payload(payload, attempted_type)

                    if response.is_success and models:
                        latency = round((time.perf_counter() - started) * 1000, 2)
                        return ProbeResult(
                            status="healthy",
                            http_status=response.status_code,
                            latency_ms=latency,
                            provider_type=detected_type,
                            models=models,
                            models_endpoint=endpoint,
                        )

                    if response.status_code in {401, 403}:
                        message = "API key ditolak atau diperlukan oleh provider."
                    elif 300 <= response.status_code < 400:
                        message = "Redirect tidak diikuti untuk melindungi API key."
                    elif response.is_success:
                        message = "Respons berhasil tetapi daftar model tidak dikenali."
                    else:
                        message = f"Endpoint model merespons HTTP {response.status_code}."
                    failures.append(
                        (
                            detected_type,
                            response.status_code,
                            message,
                        )
                    )

        latency = round((time.perf_counter() - started) * 1000, 2)
        detected = next(
            (kind for kind, _, _ in failures if kind != "unknown"), "unknown"
        )
        http_status = next(
            (code for _, code, _ in reversed(failures) if code is not None), None
        )
        reason = next(
            (message for kind, _, message in failures if kind == detected),
            "Tidak menemukan endpoint /models yang kompatibel.",
        )
        return ProbeResult(
            "down",
            http_status,
            latency,
            redact_secret(reason, secret),
            provider_type=detected,
        )
    except (httpx.InvalidURL, ValueError) as exc:
        latency = round((time.perf_counter() - started) * 1000, 2)
        return ProbeResult("down", None, latency, redact_secret(str(exc), secret))
    except Exception as exc:
        latency = round((time.perf_counter() - started) * 1000, 2)
        return ProbeResult(
            "down",
            None,
            latency,
            redact_secret(f"Pemeriksaan gagal: {exc}", secret),
        )
