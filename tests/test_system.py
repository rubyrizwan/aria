from pathlib import Path

import asyncio
import os
import subprocess

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main


def test_aria_launcher_is_executable_and_uses_english_interface():
    root = Path(__file__).resolve().parents[1]
    launcher = root / "scripts/aria"

    assert launcher.is_file()
    assert os.access(launcher, os.X_OK)
    assert not (root / "scripts/apichecker").exists()

    result = subprocess.run(
        [str(launcher), "help"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "ARIA - API Reliability & Inference Analyzer" in result.stdout
    assert "Commands:" in result.stdout
    assert "latest 60 application log lines" in result.stdout
    assert "Select an option" not in result.stdout


def test_sidebar_shows_runtime_information_and_restart_control():
    template = (
        Path(__file__).resolve().parents[1] / "app/templates/base.html"
    ).read_text()

    assert "Application running" in template
    assert "{{ server_host }}" in template
    assert "{{ server_host }}:{{ server_port }}" in template
    assert 'action="/system/restart"' in template
    assert "data-app-restart-form" in template


def test_restart_application_schedules_launcher(monkeypatch):
    calls = []

    class Process:
        def __init__(self, command, **options):
            calls.append((command, options))

    monkeypatch.setattr(main.subprocess, "Popen", Process)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/system/restart",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1),
        },
        receive=_form_receive(
            f"restart_token={main.templates.env.globals['restart_token']}".encode()
        ),
    )

    response = asyncio.run(main.restart_application(request))

    assert response.status_code == 200
    assert calls[0][0][-1] == "launcher"
    assert calls[0][1]["start_new_session"] is True


def test_restart_application_rejects_invalid_token():
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/system/restart",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
            ],
            "query_string": b"",
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1),
        },
        receive=_form_receive(b"restart_token=invalid"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.restart_application(request))

    assert exc_info.value.status_code == 403


def _form_receive(body: bytes):
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive
