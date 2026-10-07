"""Parse the RSP/RDG Fares and Associated Data Feed.

Clean an extracted feed once, then look fares up::

    from rsp_fares_feed import (
        build_fares_table,
        clean_feed,
        lookup_pair,
    )

    clean_feed(Path("raw"), Path("cleaned"))
    table = build_fares_table(Path("cleaned"))
    lookup_pair(table, "LIV", "MAN")
"""

from __future__ import annotations

from rsp_fares_feed.download import authenticate, fetch_feed, resolve_credentials
from rsp_fares_feed.errors import (
    AuthenticationError,
    DownloadError,
    FaresFeedError,
    FeedFileMissingError,
    MissingCredentialsError,
)
from rsp_fares_feed.records import (
    FLOW_CSV,
    LOCATIONS_CSV,
    TICKETS_CSV,
    clean_feed,
    cleaned_files_exist,
    find_feed_file,
    parse_flow_file,
    parse_locations_file,
    parse_ticket_types_file,
)
from rsp_fares_feed.table import (
    NON_RETAIL_SENTINEL_PENCE,
    OUTPUT_COLUMNS,
    build_fares_table,
    flag_non_retail,
    lookup_pair,
    median_advance_fare,
    retail_only,
)

__all__ = [
    "FLOW_CSV",
    "LOCATIONS_CSV",
    "NON_RETAIL_SENTINEL_PENCE",
    "OUTPUT_COLUMNS",
    "TICKETS_CSV",
    "AuthenticationError",
    "DownloadError",
    "FaresFeedError",
    "FeedFileMissingError",
    "MissingCredentialsError",
    "authenticate",
    "build_fares_table",
    "clean_feed",
    "cleaned_files_exist",
    "fetch_feed",
    "find_feed_file",
    "flag_non_retail",
    "lookup_pair",
    "median_advance_fare",
    "parse_flow_file",
    "parse_locations_file",
    "parse_ticket_types_file",
    "resolve_credentials",
    "retail_only",
]

__version__ = "0.1.0"
