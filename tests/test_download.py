from __future__ import annotations

import io
import json
import urllib.error
import zipfile
from typing import TYPE_CHECKING, Any

import pytest

from rsp_fares_feed import (
    AuthenticationError,
    DownloadError,
    MissingCredentialsError,
    authenticate,
    fetch_feed,
    resolve_credentials,
)
from rsp_fares_feed.download import AUTH_URL, FARES_URL

if TYPE_CHECKING:
    import pathlib


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class ScriptedOpener:
    """Replays a response per URL and records the requests made."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float | None = None) -> FakeResponse:
        self.requests.append(request)
        outcome = self.responses[request.full_url]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=AUTH_URL,
        code=code,
        msg="no",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b""),
    )


class TestResolveCredentials:
    def test_environment_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NRDP_USERNAME", "someone@example.com")
        monkeypatch.setenv("NRDP_PASSWORD", "hunter2")
        assert resolve_credentials() == ("someone@example.com", "hunter2")

    def test_arguments_win(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NRDP_USERNAME", "env@example.com")
        monkeypatch.setenv("NRDP_PASSWORD", "envpass")
        assert resolve_credentials("arg@example.com", "argpass") == (
            "arg@example.com",
            "argpass",
        )

    def test_missing_explains_where_to_register(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NRDP_USERNAME", raising=False)
        monkeypatch.delenv("NRDP_PASSWORD", raising=False)
        with pytest.raises(MissingCredentialsError, match=r"opendata\.nationalrail\.co\.uk"):
            resolve_credentials()


class TestAuthenticate:
    def test_returns_the_token(self) -> None:
        opener = ScriptedOpener({AUTH_URL: json.dumps({"token": "abc123"}).encode()})
        assert authenticate("u", "p", opener=opener) == "abc123"

    def test_credentials_are_form_encoded(self) -> None:
        opener = ScriptedOpener({AUTH_URL: json.dumps({"token": "abc123"}).encode()})
        authenticate("someone@example.com", "hunter2", opener=opener)
        body = opener.requests[0].data.decode()
        assert "username=someone%40example.com" in body
        assert "password=hunter2" in body

    def test_rejection(self) -> None:
        opener = ScriptedOpener({AUTH_URL: _http_error(401)})
        with pytest.raises(AuthenticationError, match="NRDP_USERNAME"):
            authenticate("u", "p", opener=opener)

    def test_no_token(self) -> None:
        opener = ScriptedOpener({AUTH_URL: json.dumps({"error": "nope"}).encode()})
        with pytest.raises(AuthenticationError, match="no token"):
            authenticate("u", "p", opener=opener)


class TestFetchFeed:
    def test_extracts_the_archive(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NRDP_USERNAME", "u")
        monkeypatch.setenv("NRDP_PASSWORD", "p")
        opener = ScriptedOpener(
            {
                AUTH_URL: json.dumps({"token": "abc123"}).encode(),
                FARES_URL: _zip_bytes({"RJFAF001.FFL": "/!! hello\n"}),
            }
        )
        destination = fetch_feed(tmp_path / "raw", opener=opener)
        assert (destination / "RJFAF001.FFL").read_text() == "/!! hello\n"

    def test_token_header(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NRDP_USERNAME", "u")
        monkeypatch.setenv("NRDP_PASSWORD", "p")
        opener = ScriptedOpener(
            {
                AUTH_URL: json.dumps({"token": "abc123"}).encode(),
                FARES_URL: _zip_bytes({"RJFAF001.FFL": ""}),
            }
        )
        fetch_feed(tmp_path / "raw", opener=opener)
        assert opener.requests[1].get_header("X-auth-token") == "abc123"

    def test_non_zip_response(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NRDP_USERNAME", "u")
        monkeypatch.setenv("NRDP_PASSWORD", "p")
        opener = ScriptedOpener(
            {
                AUTH_URL: json.dumps({"token": "abc123"}).encode(),
                FARES_URL: b"<html>not a zip</html>",
            }
        )
        with pytest.raises(DownloadError, match="not a zip"):
            fetch_feed(tmp_path / "raw", opener=opener)
