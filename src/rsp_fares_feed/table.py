"""Join the cleaned feed into a queryable fares table.

The feed's own files are awkward to use. Flows address stations by NLC,
fares address flows by ID, and ticket codes are three letters with no
meaning on their own. This joins all three into one row per (origin
CRS, destination CRS, ticket type, restriction) with a price.

It also flags fares that are not retail prices at all; see
:func:`flag_non_retail`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

from rsp_fares_feed.records import FLOW_CSV, LOCATIONS_CSV, TICKETS_CSV

if TYPE_CHECKING:
    import pathlib

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS = [
    "origin",
    "origin_name",
    "destination",
    "destination_name",
    "ticket_code",
    "ticket_description",
    "tkt_class",
    "tkt_type",
    "fare_pence",
    "fare_gbp",
    "restriction_code",
    "toc",
    "route_code",
    "non_retail_fare",
]

# Round-number placeholder prices. Negotiated products with no
# advertised price (Group, Tour Operator, School Group, Special Events,
# Family, operator complimentary travel) carry one of these on every
# route in the country instead of a distance-based amount. Checked
# against the full national feed.
NON_RETAIL_SENTINEL_PENCE = frozenset({99900, 99999, 999800})  # £999.00 / £999.99 / £9,998.00

# A ticket code also counts as non-retail if it has almost the same
# price on enough routes to rule out coincidence. Wholesale and
# settlement products (Inclusive Tour, staff, child concession) carry a
# token price unrelated to distance. A real distance-based fare would
# take far more than a handful of values across this many routes.
NON_RETAIL_FLAT_MIN_ROUTES = 20
NON_RETAIL_FLAT_MAX_UNIQUE_PRICES = 3
NON_RETAIL_FLAT_MAX_PENCE = 20

# Advance fares are a yield-managed ladder, one row per price point,
# all described as ADVANCE. Standard class only.
ADVANCE_DESCRIPTION = "ADVANCE"
STANDARD_CLASS = "2"


def flag_non_retail(flow: pd.DataFrame) -> pd.Series:
    """Flag rows that are not real point-to-point retail prices.

    The fares feed holds far more than the prices a passenger can buy.
    Two patterns account for nearly all of the rest:

    1. A placeholder price, one of :data:`NON_RETAIL_SENTINEL_PENCE`,
       for products negotiated case by case.
    2. A flat low price across many routes, which is a settlement token
       rather than a fare. The constants above set the thresholds.

    Returns a boolean Series aligned to *flow*. Nothing is dropped; the
    flag becomes a column and the caller decides.
    """
    sentinel = flow["fare_pence"].isin(NON_RETAIL_SENTINEL_PENCE)

    per_ticket = flow.groupby("ticket_code")["fare_pence"].agg(
        distinct_prices="nunique", routes="count", highest="max"
    )
    flat_codes = per_ticket[
        (per_ticket["distinct_prices"] <= NON_RETAIL_FLAT_MAX_UNIQUE_PRICES)
        & (per_ticket["routes"] >= NON_RETAIL_FLAT_MIN_ROUTES)
        & (per_ticket["highest"] <= NON_RETAIL_FLAT_MAX_PENCE)
    ].index

    return sentinel | flow["ticket_code"].isin(flat_codes)


def build_fares_table(cleaned_dir: pathlib.Path) -> pd.DataFrame:
    """Join the three cleaned CSVs into one fares table.

    The returned frame carries :data:`OUTPUT_COLUMNS` plus
    ``direction``, which :func:`lookup_pair` needs to resolve reversible
    flows.
    """
    flow = pd.read_csv(cleaned_dir / FLOW_CSV, dtype=str)
    flow["fare_pence"] = pd.to_numeric(flow["fare_pence"], errors="coerce")
    flow["non_retail_fare"] = flag_non_retail(flow)

    locations = pd.read_csv(cleaned_dir / LOCATIONS_CSV, dtype=str)
    tickets = pd.read_csv(cleaned_dir / TICKETS_CSV, dtype=str)

    origins = locations.rename(
        columns={
            "nlc_code": "origin_code",
            "crs_code": "origin",
            "description": "origin_name",
        }
    )
    destinations = locations.rename(
        columns={
            "nlc_code": "destination_code",
            "crs_code": "destination",
            "description": "destination_name",
        }
    )

    table = flow.merge(origins, on="origin_code", how="inner")
    table = table.merge(destinations, on="destination_code", how="inner")
    table = table.merge(
        tickets.rename(columns={"description": "ticket_description"}),
        on="ticket_code",
        how="left",
    )
    table["fare_gbp"] = table["fare_pence"] / 100
    return table[[*OUTPUT_COLUMNS, "direction"]]


def lookup_pair(table: pd.DataFrame, origin: str, destination: str) -> pd.DataFrame:
    """Fares for one origin to destination pair, cheapest first.

    A flow marked reversible (``direction == "R"``) also prices the
    journey back the other way. Those rows are found and relabelled, so
    the result always reads as *origin* to *destination* whichever way
    round the underlying flow record was written.
    """
    origin = origin.strip().upper()
    destination = destination.strip().upper()

    forward = table[(table["origin"] == origin) & (table["destination"] == destination)]

    reverse = table[
        (table["direction"] == "R")
        & (table["origin"] == destination)
        & (table["destination"] == origin)
    ].copy()
    if not reverse.empty:
        # One rename() applies the whole mapping against the original
        # labels at once, so swapping both ways round in a single call
        # is safe.
        reverse = reverse.rename(
            columns={
                "origin": "destination",
                "destination": "origin",
                "origin_name": "destination_name",
                "destination_name": "origin_name",
            }
        )

    matches = pd.concat([forward, reverse], ignore_index=True)
    matches = matches.drop_duplicates(subset=["ticket_code", "restriction_code", "fare_pence"])
    return matches[OUTPUT_COLUMNS].sort_values("fare_pence").reset_index(drop=True)


def median_advance_fare(table: pd.DataFrame, origin: str, destination: str) -> float | None:
    """Median standard-class adult Advance fare for a pair, in pounds.

    There is no single Advance price. The feed publishes each rung of an
    operator's yield-managed ladder: codes such as ``DG0`` to ``DG9``,
    ``V2A`` to ``V2Z`` and ``USA`` to ``USZ`` are one product at
    different prices, and what a passenger pays depends on how far ahead
    they book and what is left. The median across the published ladder
    is a reasonable stand-in for the expected price in revenue work.

    Restricted to standard-class retail fares described exactly as
    ``ADVANCE``: first class (``ADVANCE 1ST``), standard premium
    (``ADVANCE STDPREM``) and the non-retail placeholders are excluded,
    so this is the ordinary adult Advance single.

    Returns ``None`` if the pair has no such fare.
    """
    fares = lookup_pair(table, origin, destination)
    if fares.empty:
        return None

    description = fares["ticket_description"].astype(str).str.strip().str.upper()
    ticket_class = fares["tkt_class"].astype(str).str.strip()
    advance = fares[
        (description == ADVANCE_DESCRIPTION)
        & (ticket_class == STANDARD_CLASS)
        & (~fares["non_retail_fare"])
    ]
    if advance.empty:
        return None
    return float(advance["fare_gbp"].median())


def retail_only(fares: pd.DataFrame) -> pd.DataFrame:
    """Drop the rows flagged as non-retail."""
    return fares[~fares["non_retail_fare"]]
