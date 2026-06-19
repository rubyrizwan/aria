from starlette.requests import Request

from app.main import APP_VERSION, about_page, app


def request_for(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "GET",
            "path": path,
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 1),
        }
    )


def test_about_page_shows_current_product_information():
    response = about_page(request_for("/about"))

    assert f"v{APP_VERSION}".encode() in response.body
    assert b"What API Checker does" in response.body
    assert b"Recommended workflow" in response.body
    assert b"Load models" in response.body
    assert b"Test model access" in response.body
    assert b"Available Models" in response.body
    assert b"Security and privacy" in response.body
    assert b"Ruby Rizwan" in response.body
    assert b"rzwan182@gmail.com" in response.body
    assert b"https://saweria.co/rubydevara" in response.body


def test_about_page_shows_recent_release_history():
    response = about_page(request_for("/about"))

    assert b"Version history" in response.body
    assert b"v0.4.1" in response.body
    assert b"v0.4.0" in response.body
    assert b"v0.3.1" in response.body
    assert b'release-version current' in response.body
