"""Turn the cleaned feed into fares you can look up by station.

The feed's own files are awkward to use. Flows address stations by NLC,
fares address flows by ID, and ticket codes are three letters with no
meaning on their own. About half the fares are set for a cluster or
group of stations rather than one station, so a fare from Liverpool to
Manchester may be filed under neither station's own code.
:class:`FaresTable` keeps the fares by NLC and knows every code each
station is priced under, and :func:`lookup_pair` searches all of them.

It also flags fares that are not retail prices at all; see
:func:`flag_non_retail`.
"""

from __future__ import annotations

import collections
import dataclasses
import logging
from typing import TYPE_CHECKING

import pandas as pd

from rsp_fares_feed.records import CLUSTERS_CSV, FLOW_CSV, LOCATIONS_CSV, TICKETS_CSV

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

# The ordinary adult products, matched on the start of the ticket
# description. The feed carries hundreds of others on the same flows
# (family, small group, child, "Kid with Adult" at £2, "SALE ADVANCE" at
# £1, pay-as-you-go test entries), too many to exclude one by one.
ADULT_PRODUCTS = (
    "ADVANCE",
    "ANYTIME",
    "OFF-PEAK",
    "SUPER OFFPEAK",
    "SUP OFFPK",
    "LUMOFIXED",
    "SEMI FLEX",
)

# Below this a standard-class fare is a token, not a price anyone pays.
MIN_RETAIL_FARE_GBP = 1.0


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


@dataclasses.dataclass
class FaresTable:
    """The cleaned feed, ready to query by station CRS.

    ``fares`` has one row per fare, keyed by NLC: a station's own, a
    cluster's or a group's. ``codes`` maps each CRS to every NLC it is
    priced under, and ``names`` maps each CRS to its name.
    """

    fares: pd.DataFrame
    codes: dict[str, frozenset[str]]
    names: dict[str, str]


def build_fares_table(cleaned_dir: pathlib.Path) -> FaresTable:
    """Load the cleaned CSVs into a :class:`FaresTable`.

    A folder cleaned by version 0.1, which has no clusters file, still
    loads, but finds only fares set for single stations.
    """
    flow = pd.read_csv(cleaned_dir / FLOW_CSV, dtype=str)
    flow["fare_pence"] = pd.to_numeric(flow["fare_pence"], errors="coerce")
    flow["non_retail_fare"] = flag_non_retail(flow)
    tickets = pd.read_csv(cleaned_dir / TICKETS_CSV, dtype=str)
    flow = flow.merge(
        tickets.rename(columns={"description": "ticket_description"}),
        on="ticket_code",
        how="left",
    )
    flow["fare_gbp"] = flow["fare_pence"] / 100

    locations = pd.read_csv(cleaned_dir / LOCATIONS_CSV, dtype=str)
    clusters_path = cleaned_dir / CLUSTERS_CSV
    if clusters_path.exists():
        clusters = pd.read_csv(clusters_path, dtype=str)
    else:
        logger.warning(
            "No %s in %s; clean the feed again to find cluster fares",
            CLUSTERS_CSV,
            cleaned_dir,
        )
        clusters = pd.DataFrame(columns=["cluster_code", "nlc_code"])
    member_of: dict[str, set[str]] = collections.defaultdict(set)
    for cluster, member in zip(clusters["cluster_code"], clusters["nlc_code"], strict=True):
        member_of[member].add(cluster)

    codes: dict[str, set[str]] = collections.defaultdict(set)
    for crs, nlc in zip(locations["crs_code"], locations["nlc_code"], strict=True):
        # A group (London Terminals) can itself sit in a cluster.
        groups = member_of.get(nlc, set())
        codes[crs] |= {nlc, *groups, *(c for g in groups for c in member_of.get(g, ()))}
    names = dict(zip(locations["crs_code"], locations["description"], strict=True))
    return FaresTable(flow, {k: frozenset(v) for k, v in codes.items()}, names)


def lookup_pair(table: FaresTable, origin: str, destination: str) -> pd.DataFrame:
    """Every fare from *origin* to *destination*, cheapest first.

    A fare matches when it is set between any codes the two stations
    are priced under: their own NLCs, their clusters or their groups. A
    flow marked reversible (``direction == "R"``) also prices the
    journey back, and the result always reads *origin* to *destination*.
    """
    origin = origin.strip().upper()
    destination = destination.strip().upper()
    fares = table.fares
    o = table.codes.get(origin, frozenset())
    d = table.codes.get(destination, frozenset())
    forward = fares["origin_code"].isin(o) & fares["destination_code"].isin(d)
    reverse = (
        (fares["direction"] == "R")
        & fares["origin_code"].isin(d)
        & fares["destination_code"].isin(o)
    )
    matches = fares[forward | reverse].assign(
        origin=origin,
        destination=destination,
        origin_name=table.names.get(origin),
        destination_name=table.names.get(destination),
    )
    matches = matches.drop_duplicates(
        subset=["ticket_code", "restriction_code", "fare_pence", "route_code"]
    )
    return matches[OUTPUT_COLUMNS].sort_values("fare_pence").reset_index(drop=True)


def median_advance_fare(table: FaresTable, origin: str, destination: str) -> float | None:
    """Median standard-class adult Advance fare for a pair, in pounds.

    There is no single Advance price. The feed publishes each rung of an
    operator's yield-managed ladder: codes such as ``DG0`` to ``DG9``,
    ``V2A`` to ``V2Z`` and ``USA`` to ``USZ`` are one product at
    different prices, and what a passenger pays depends on how far ahead
    they book and what is left. The median across the published ladder
    is a fair stand-in for the expected price in revenue work.

    Only standard-class retail fares described exactly as ``ADVANCE``
    count, so first class (``ADVANCE 1ST``), Standard Premium
    (``ADVANCE STDPREM``) and the placeholders are left out. Returns
    ``None`` if the pair has no such fare.
    """
    fares = lookup_pair(table, origin, destination)
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


def median_fare(table: FaresTable, origin: str, destination: str) -> float | None:
    """Median adult standard fare per journey for a pair, in pounds.

    The feed has no sales figures, so each ordinary product
    (:data:`ADULT_PRODUCTS`: the Advance rungs, Anytime, Off-Peak and
    Super Off-Peak) counts once. Singles count at face value and
    returns at half. Seasons, first class, Standard Premium, the
    placeholder prices and anything under :data:`MIN_RETAIL_FARE_GBP`
    are left out, and the median takes care of what is left in the
    tails. Returns ``None`` if the pair has no such fare.
    """
    fares = lookup_pair(table, origin, destination)
    description = fares["ticket_description"].fillna("").str.strip().str.upper()
    retail = fares[
        (fares["tkt_class"].astype(str).str.strip() == STANDARD_CLASS)
        & fares["tkt_type"].isin(("S", "R"))
        & ~fares["non_retail_fare"].astype(bool)
        & description.str.startswith(ADULT_PRODUCTS)
        & ~description.str.contains("STDPREM")
    ]
    per_journey = retail["fare_gbp"].where(retail["tkt_type"] == "S", retail["fare_gbp"] / 2)
    per_journey = per_journey[per_journey >= MIN_RETAIL_FARE_GBP]
    return float(per_journey.median()) if not per_journey.empty else None


def retail_only(fares: pd.DataFrame) -> pd.DataFrame:
    """Drop the rows flagged as non-retail."""
    return fares[~fares["non_retail_fare"]]
