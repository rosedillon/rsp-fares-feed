"""Download the fares feed from the National Rail Data Portal.

Authentication takes two steps, as the NRDP article on the Open Rail
Data wiki describes. POST the credentials to ``/authenticate`` for a
token, then GET the feed with that token in an ``X-Auth-Token`` header.
The response is a zip of the whole feed, around 300 MB.
"""

from __future__ import annotations

import io
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from typing import TYPE_CHECKING, Any

from rsp_fares_feed.errors import AuthenticationError, DownloadError, MissingCredentialsError

if TYPE_CHECKING:
    import pathlib

logger = logging.getLogger(__name__)

AUTH_URL = "https://opendata.nationalrail.co.uk/authenticate"
FARES_URL = "https://opendata.nationalrail.co.uk/api/staticfeeds/2.0/fares"

DEFAULT_TIMEOUT_SECONDS = 300

USERNAME_ENV = "NRDP_USERNAME"
PASSWORD_ENV = "NRDP_PASSWORD"


def resolve_credentials(
    username: str | None = None, password: str | None = None
) -> tuple[str, str]:
    """Credentials from the arguments, else from the environment."""
    resolved_username = username or os.environ.get(USERNAME_ENV)
    resolved_password = password or os.environ.get(PASSWORD_ENV)
    if not resolved_username or not resolved_password:
        raise MissingCredentialsError
    return resolved_username, resolved_password


def authenticate(
    username: str,
    password: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Any = None,
) -> str:
    """Exchange credentials for a bearer token."""
    open_url = opener or urllib.request.urlopen
    body = urllib.parse.urlencode({"username": username, "password": password}).encode()
    request = urllib.request.Request(
        AUTH_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with open_url(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise AuthenticationError(
            f"NRDP rejected the credentials (HTTP {error.code}). Check "
            f"{USERNAME_ENV} and {PASSWORD_ENV}."
        ) from error

    token = payload.get("token")
    if not token:
        raise AuthenticationError(
            f"NRDP authenticated but returned no token; the response was {sorted(payload)}."
        )
    return str(token)


def fetch_feed(
    destination: pathlib.Path,
    username: str | None = None,
    password: str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Any = None,
) -> pathlib.Path:
    """Download the fares feed and extract it into *destination*.

    Credentials fall back to ``NRDP_USERNAME`` and ``NRDP_PASSWORD``.
    Returns *destination*, so this chains into
    :func:`~rsp_fares_feed.records.clean_feed`.
    """
    resolved_username, resolved_password = resolve_credentials(username, password)
    open_url = opener or urllib.request.urlopen

    logger.info("Authenticating with the National Rail Data Portal")
    token = authenticate(resolved_username, resolved_password, timeout=timeout, opener=open_url)

    logger.info("Downloading the fares feed (this is a few hundred MB)")
    request = urllib.request.Request(FARES_URL, headers={"X-Auth-Token": token})
    try:
        with open_url(request, timeout=timeout) as response:
            archive_bytes = response.read()
    except urllib.error.HTTPError as error:
        raise DownloadError(f"Downloading the fares feed failed: HTTP {error.code}") from error

    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            archive.extractall(destination)
    except zipfile.BadZipFile as error:
        raise DownloadError(
            "The download was not a zip archive. This usually means the token "
            "expired or the account lacks access to the fares feed."
        ) from error

    logger.info("Extracted the fares feed to %s", destination)
    return destination
