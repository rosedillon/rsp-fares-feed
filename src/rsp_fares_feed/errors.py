"""Exceptions raised by this package."""

from __future__ import annotations


class FaresFeedError(Exception):
    """Base class for every error this package raises."""


class MissingCredentialsError(FaresFeedError):
    """No National Rail Data Portal credentials could be found."""

    def __init__(self) -> None:
        super().__init__(
            "No National Rail Data Portal credentials found. Set the NRDP_USERNAME "
            "and NRDP_PASSWORD environment variables, or pass username= and "
            "password= explicitly. Register free at "
            "https://opendata.nationalrail.co.uk."
        )


class AuthenticationError(FaresFeedError):
    """NRDP rejected the credentials, or returned no token."""


class DownloadError(FaresFeedError):
    """The feed could not be downloaded."""


class FeedFileMissingError(FaresFeedError, FileNotFoundError):
    """An expected file is not present in the extracted feed."""

    def __init__(self, suffix: str, directory: object) -> None:
        super().__init__(
            f"No *{suffix} file in {directory}. The fares feed archive should contain "
            f"one (named like RJFAF882{suffix}). Check the directory is an extracted "
            "feed and not the .zip."
        )
