"""Command-line interface.

There are three steps: ``download`` the feed (which needs NRDP
credentials), ``clean`` it into CSVs, then ``lookup`` fares between
stations. Credentials are read only from the environment.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import TYPE_CHECKING

from rsp_fares_feed.download import fetch_feed
from rsp_fares_feed.errors import FaresFeedError
from rsp_fares_feed.records import clean_feed, cleaned_files_exist
from rsp_fares_feed.table import (
    build_fares_table,
    lookup_pair,
    median_advance_fare,
    retail_only,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

DISPLAY_COLUMNS = [
    "ticket_code",
    "ticket_description",
    "fare_gbp",
    "restriction_code",
    "toc",
    "route_code",
]


def _cmd_download(args: argparse.Namespace) -> int:
    fetch_feed(args.output)
    print(f"Extracted the feed to {args.output}", file=sys.stderr)
    return 0


def _cmd_clean(args: argparse.Namespace) -> int:
    if not args.raw.exists():
        print(f"error: {args.raw} not found", file=sys.stderr)
        return 1
    counts = clean_feed(args.raw, args.output)
    if args.json:
        json.dump(counts, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        for name, count in counts.items():
            print(f"{name:<10} {count:>10,}")
    return 0


def _cmd_lookup(args: argparse.Namespace) -> int:
    if not cleaned_files_exist(args.directory):
        print(
            f"error: no cleaned feed in {args.directory}. Run "
            f"`rsp-fares-feed clean <raw-dir> -o {args.directory}` first.",
            file=sys.stderr,
        )
        return 1

    table = build_fares_table(args.directory)
    fares = lookup_pair(table, args.origin, args.destination)
    if fares.empty:
        print(
            f"No fares found for {args.origin.upper()} -> {args.destination.upper()}. "
            "Fares set only at cluster level are not resolvable here (see the README).",
            file=sys.stderr,
        )
        return 0

    retail = fares if args.include_non_retail else retail_only(fares)
    flagged = len(fares) - len(retail)
    if flagged:
        print(
            f"note: {flagged} of {len(fares)} fare(s) are non-retail placeholders "
            "(group, tour operator, settlement tokens) and are hidden; pass "
            "--include-non-retail to show them.",
            file=sys.stderr,
        )

    if args.csv:
        retail.to_csv(args.csv, index=False)
        print(f"Wrote {len(retail):,} fare(s) to {args.csv}", file=sys.stderr)
        return 0

    if args.json:
        json.dump(
            {
                "origin": args.origin.upper(),
                "destination": args.destination.upper(),
                "median_advance_gbp": median_advance_fare(table, args.origin, args.destination),
                "fares": retail[DISPLAY_COLUMNS].to_dict(orient="records"),
            },
            sys.stdout,
            indent=2,
            default=str,
        )
        sys.stdout.write("\n")
        return 0

    if retail.empty:
        print("No retail fares for this pair (only non-retail placeholders).")
        return 0

    print(f"{args.origin.upper()} -> {args.destination.upper()}")
    print(retail[DISPLAY_COLUMNS].to_string(index=False))
    advance = median_advance_fare(table, args.origin, args.destination)
    if advance is not None:
        print(f"\nMedian standard-class Advance: £{advance:.2f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rsp-fares-feed",
        description=(
            "Download and parse the RSP fares feed (RSPS5045), then look up fares "
            "between stations. Downloading needs a National Rail Data Portal account; "
            "set NRDP_USERNAME and NRDP_PASSWORD."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log progress")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser(
        "download",
        help="Fetch and extract the feed from NRDP.",
        description="Authenticate with NRDP, download the fares feed and extract it.",
    )
    download.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("fares-raw"),
        help="directory to extract into (default: ./fares-raw)",
    )
    download.set_defaults(func=_cmd_download)

    clean = subparsers.add_parser(
        "clean",
        help="Parse an extracted feed into CSVs.",
        description="Parse the .FFL, .LOC and .TTY files into three cleaned CSVs.",
    )
    clean.add_argument("raw", type=pathlib.Path, help="directory of the extracted feed")
    clean.add_argument(
        "-o",
        "--output",
        type=pathlib.Path,
        default=pathlib.Path("fares"),
        help="directory for the cleaned CSVs (default: ./fares)",
    )
    clean.add_argument("--json", action="store_true", help="emit counts as JSON")
    clean.set_defaults(func=_cmd_clean)

    lookup = subparsers.add_parser(
        "lookup",
        help="Fares between two stations.",
        description="Look up published fares between two stations, cheapest first.",
    )
    lookup.add_argument("origin", help="origin CRS code, e.g. LIV")
    lookup.add_argument("destination", help="destination CRS code, e.g. MAN")
    lookup.add_argument(
        "-d",
        "--directory",
        type=pathlib.Path,
        default=pathlib.Path("fares"),
        help="directory of cleaned CSVs (default: ./fares)",
    )
    lookup.add_argument(
        "--include-non-retail",
        action="store_true",
        help="also show group, tour-operator and settlement placeholder prices",
    )
    lookup.add_argument("--csv", metavar="PATH", help="write the fares to a CSV file")
    lookup.add_argument("--json", action="store_true", help="emit JSON")
    lookup.set_defaults(func=_cmd_lookup)

    # -v also works after the subcommand.
    for subcommand in subparsers.choices.values():
        subcommand.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            default=argparse.SUPPRESS,
            help="log progress",
        )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        return int(args.func(args))
    except FaresFeedError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
