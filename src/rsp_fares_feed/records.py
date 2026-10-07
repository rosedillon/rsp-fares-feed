"""Fixed-width record parsing for the RSP fares feed.

The Rail Delivery Group and Rail Settlement Plan publish the feed, and
the National Rail Data Portal distributes it. Every file is fixed-format
text: one record per line, a leading ``/`` for a comment, and fields
identified only by character position. The positions below come from
the interface specification, RSPS5045 ("Fares and Associated Data Feed
Interface Specification"). Its 1-indexed inclusive "Position" column
becomes a 0-indexed Python slice here.

Three of the feed's two dozen files are read:

``.FFL``
    Flow and Fare records. A Flow record (``RF``) describes a route
    between two NLC codes, and Fare records (``RT``) refer to it by flow
    ID, one per ticket type. Joining them gives a price per ticket type
    per route.
``.LOC``
    Location records (``RL``): the NLC to CRS and name lookup that turns
    a flow's numeric codes into station codes.
``.TTY``
    Ticket type records: what ``SOS``, ``PAP`` and the rest mean.

Records carrying an update marker of ``D`` are deletions from a change
file and are dropped.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

from rsp_fares_feed.errors import FeedFileMissingError

if TYPE_CHECKING:
    import pathlib

logger = logging.getLogger(__name__)

# Update marker meaning "this record is deleted".
DELETE_MARKER = "D"

# Character spans, 0-indexed with exclusive end, from RSPS5045.
FLOW_FIELDS: dict[str, tuple[int, int]] = {
    "update_marker": (0, 1),
    "record_type": (1, 2),
    "origin_code": (2, 6),
    "destination_code": (6, 10),
    "route_code": (10, 15),
    "status_code": (15, 18),
    "usage_code": (18, 19),
    "direction": (19, 20),
    "end_date": (20, 28),
    "start_date": (28, 36),
    "toc": (36, 39),
    "cross_london_ind": (39, 40),
    "flow_id": (42, 49),
}

FARE_FIELDS: dict[str, tuple[int, int]] = {
    "update_marker": (0, 1),
    "record_type": (1, 2),
    "flow_id": (2, 9),
    "ticket_code": (9, 12),
    "fare_pence": (12, 20),
    "restriction_code": (20, 22),
}

LOCATION_FIELDS: dict[str, tuple[int, int]] = {
    "update_marker": (0, 1),
    "record_type": (1, 2),
    "nlc_code": (36, 40),
    "description": (40, 56),
    "crs_code": (56, 59),
}

TICKET_TYPE_FIELDS: dict[str, tuple[int, int]] = {
    "update_marker": (0, 1),
    "ticket_code": (1, 4),
    "description": (28, 43),
    "tkt_class": (43, 44),
    "tkt_type": (44, 45),
    "tkt_group": (45, 46),
}

FLOW_COLUMNS = [
    "origin_code",
    "destination_code",
    "route_code",
    "direction",
    "toc",
    "cross_london_ind",
    "ticket_code",
    "fare_pence",
    "restriction_code",
]
LOCATION_COLUMNS = ["nlc_code", "crs_code", "description"]
TICKET_COLUMNS = ["ticket_code", "description", "tkt_class", "tkt_type", "tkt_group"]

# Filenames written by `clean_feed`.
FLOW_CSV = "fares_flow.csv"
LOCATIONS_CSV = "fares_locations.csv"
TICKETS_CSV = "fares_tickets.csv"


def _fields(line: str, spec: dict[str, tuple[int, int]]) -> dict[str, str]:
    return {name: line[start:end].strip() for name, (start, end) in spec.items()}


def _is_comment(line: str) -> bool:
    return not line or line.startswith("/")


def find_feed_file(directory: pathlib.Path, suffix: str) -> pathlib.Path:
    """The single file with *suffix* in *directory*.

    The feed names its files by sequence number (``RJFAF882.FFL``), so
    they are found by extension rather than by name.
    """
    matches = sorted(directory.glob(f"*{suffix}"))
    if not matches:
        raise FeedFileMissingError(suffix, directory)
    if len(matches) > 1:
        logger.warning("More than one *%s in %s; using %s", suffix, directory, matches[0].name)
    return matches[0]


def parse_flow_file(path: pathlib.Path) -> pd.DataFrame:
    """Parse a ``.FFL`` into one row per fare.

    A Flow record holds the route and the Fare records hold the prices.
    Neither is useful alone, so they are joined on flow ID, giving one
    row per (route, ticket type, restriction).
    """
    flows: dict[str, dict[str, str]] = {}
    fares: list[dict[str, str]] = []

    with path.open(encoding="latin-1") as handle:
        for line in handle:
            if _is_comment(line):
                continue
            record_type = line[1:2]
            if record_type == "F":
                row = _fields(line, FLOW_FIELDS)
                if row["update_marker"] != DELETE_MARKER:
                    flows[row["flow_id"]] = row
            elif record_type == "T":
                row = _fields(line, FARE_FIELDS)
                if row["update_marker"] != DELETE_MARKER:
                    fares.append(row)

    if not fares:
        return pd.DataFrame(columns=FLOW_COLUMNS)

    fare_frame = pd.DataFrame(fares)
    flow_frame = pd.DataFrame.from_dict(flows, orient="index")
    if flow_frame.empty:
        return pd.DataFrame(columns=FLOW_COLUMNS)

    joined = fare_frame.merge(flow_frame, on="flow_id", how="inner", suffixes=("", "_flow"))
    joined["fare_pence"] = pd.to_numeric(joined["fare_pence"], errors="coerce")
    return joined[FLOW_COLUMNS].dropna(subset=["fare_pence"])


def parse_locations_file(path: pathlib.Path) -> pd.DataFrame:
    """Parse a ``.LOC`` into an NLC to CRS/name lookup.

    Entries with no NLC or no CRS are dropped. The location file also
    holds Travelcard zones, fare groups and places outside Great
    Britain, and none of those resolve a flow to a station.
    """
    rows = []
    with path.open(encoding="latin-1") as handle:
        for line in handle:
            if _is_comment(line) or line[1:2] != "L":
                continue
            row = _fields(line, LOCATION_FIELDS)
            if row["update_marker"] != DELETE_MARKER:
                rows.append(row)

    if not rows:
        return pd.DataFrame(columns=LOCATION_COLUMNS)

    locations = pd.DataFrame(rows)
    locations = locations[(locations["nlc_code"] != "") & (locations["crs_code"] != "")]
    return locations[LOCATION_COLUMNS].drop_duplicates("nlc_code")


def parse_ticket_types_file(path: pathlib.Path) -> pd.DataFrame:
    """Parse a ``.TTY`` into a ticket-code lookup."""
    rows = []
    with path.open(encoding="latin-1") as handle:
        for line in handle:
            if _is_comment(line):
                continue
            row = _fields(line, TICKET_TYPE_FIELDS)
            if row["update_marker"] != DELETE_MARKER:
                rows.append(row)

    if not rows:
        return pd.DataFrame(columns=TICKET_COLUMNS)
    return pd.DataFrame(rows)[TICKET_COLUMNS].drop_duplicates("ticket_code")


def clean_feed(raw_dir: pathlib.Path, output_dir: pathlib.Path) -> dict[str, int]:
    """Parse an extracted feed and write the three cleaned CSVs.

    Returns the row count written for each.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Parsing flow and fare records")
    flow = parse_flow_file(find_feed_file(raw_dir, ".FFL"))
    flow.to_csv(output_dir / FLOW_CSV, index=False)

    logger.info("Parsing location records")
    locations = parse_locations_file(find_feed_file(raw_dir, ".LOC"))
    locations.to_csv(output_dir / LOCATIONS_CSV, index=False)

    logger.info("Parsing ticket type records")
    tickets = parse_ticket_types_file(find_feed_file(raw_dir, ".TTY"))
    tickets.to_csv(output_dir / TICKETS_CSV, index=False)

    counts = {"flow": len(flow), "locations": len(locations), "tickets": len(tickets)}
    logger.info("Cleaned fares feed: %s", ", ".join(f"{k}={v:,}" for k, v in counts.items()))
    return counts


def cleaned_files_exist(directory: pathlib.Path) -> bool:
    """Whether *directory* already holds all three cleaned CSVs."""
    return all((directory / name).exists() for name in (FLOW_CSV, LOCATIONS_CSV, TICKETS_CSV))
