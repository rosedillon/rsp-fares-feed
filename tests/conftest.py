"""Synthetic RSP fares feed files.

The real feed is about 300 MB and needs a National Rail Data Portal
account, so these build small fixed-width files at exactly the
character positions `rsp_fares_feed.records` reads. Real records look
like::

    RF0027003201000000AS3112299902032025ATO01Y0000003
    RT0000001PAP00000530PF
    RL700027001092025070820252609202470 0027TRVCARD 1          00000

so the records below are padded to the same geometry. Change a slice in
the parser without changing it here and the assertions fail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rsp_fares_feed import records

if TYPE_CHECKING:
    import pathlib


def _place(spec: dict[str, tuple[int, int]], values: dict[str, str]) -> str:
    """Build a fixed-width line, writing each value at its span."""
    width = max(end for _, end in spec.values())
    line = [" "] * width
    for name, value in values.items():
        start, end = spec[name]
        field = value.ljust(end - start)[: end - start]
        line[start:end] = field
    return "".join(line)


def flow_record(
    flow_id: str,
    origin_code: str,
    destination_code: str,
    *,
    direction: str = "S",
    route_code: str = "01000",
    toc: str = "ATO",
    update_marker: str = "R",
) -> str:
    return _place(
        records.FLOW_FIELDS,
        {
            "update_marker": update_marker,
            "record_type": "F",
            "origin_code": origin_code,
            "destination_code": destination_code,
            "route_code": route_code,
            "status_code": "000",
            "usage_code": "A",
            "direction": direction,
            "end_date": "31122999",
            "start_date": "02032025",
            "toc": toc,
            "cross_london_ind": "0",
            "flow_id": flow_id,
        },
    )


def fare_record(
    flow_id: str,
    ticket_code: str,
    fare_pence: int,
    *,
    restriction_code: str = "PF",
    update_marker: str = "R",
) -> str:
    return _place(
        records.FARE_FIELDS,
        {
            "update_marker": update_marker,
            "record_type": "T",
            "flow_id": flow_id,
            "ticket_code": ticket_code,
            "fare_pence": str(fare_pence).zfill(8),
            "restriction_code": restriction_code,
        },
    )


def location_record(
    nlc_code: str, crs_code: str, description: str, *, update_marker: str = "R"
) -> str:
    return _place(
        records.LOCATION_FIELDS,
        {
            "update_marker": update_marker,
            "record_type": "L",
            "nlc_code": nlc_code,
            "description": description,
            "crs_code": crs_code,
        },
    )


def ticket_record(
    ticket_code: str,
    description: str,
    *,
    tkt_class: str = "2",
    tkt_type: str = "S",
    tkt_group: str = "S",
    update_marker: str = "R",
) -> str:
    return _place(
        records.TICKET_TYPE_FIELDS,
        {
            "update_marker": update_marker,
            "ticket_code": ticket_code,
            "description": description,
            "tkt_class": tkt_class,
            "tkt_type": tkt_type,
            "tkt_group": tkt_group,
        },
    )


# Two stations, plus a Travelcard zone with no CRS that must be dropped.
LIVERPOOL_NLC = "1444"
MANCHESTER_NLC = "2968"
LEEDS_NLC = "8446"


@pytest.fixture
def raw_feed(tmp_path: pathlib.Path) -> pathlib.Path:
    """A directory of synthetic .FFL / .LOC / .TTY files."""
    directory = tmp_path / "raw"
    directory.mkdir()

    flow_lines = [
        "/!! Start of file",
        # LIV -> MAN, one-directional, three ticket types.
        flow_record("0000001", LIVERPOOL_NLC, MANCHESTER_NLC),
        fare_record("0000001", "SOS", 1_450),
        fare_record("0000001", "SOR", 2_310),
        fare_record("0000001", "GRP", 99_900),  # sentinel placeholder price
        # LEEDS -> MAN, reversible, so it also prices MAN -> LEEDS.
        flow_record("0000002", LEEDS_NLC, MANCHESTER_NLC, direction="R"),
        fare_record("0000002", "SOS", 1_890),
        # A deleted flow and a deleted fare; both must be ignored.
        flow_record("0000003", LIVERPOOL_NLC, LEEDS_NLC, update_marker="D"),
        fare_record("0000001", "XXX", 500, update_marker="D"),
        # A fare whose flow does not exist: dropped by the inner join.
        fare_record("9999999", "SOS", 700),
    ]
    # A settlement token: flat, low, and on many routes.
    for index in range(25):
        flow_id = f"001{index:04d}"
        flow_lines.append(flow_record(flow_id, LIVERPOOL_NLC, f"{3000 + index:04d}"))
        flow_lines.append(fare_record(flow_id, "TKN", 1))
    (directory / "RJFAF001.FFL").write_text("\n".join(flow_lines) + "\n", encoding="latin-1")

    location_lines = [
        "/!! Start of file",
        location_record(LIVERPOOL_NLC, "LIV", "LIVERPOOL LSP"),
        location_record(MANCHESTER_NLC, "MAN", "MANCHESTER PIC"),
        location_record(LEEDS_NLC, "LDS", "LEEDS"),
        # One of the settlement-token destinations, so that a TKN row
        # survives the join and the non-retail flag can be observed on
        # the built table.
        location_record("3000", "TKD", "TOKEN DEST"),
        # A Travelcard zone: an NLC with no CRS, which is not a station.
        location_record("0027", "", "TRVCARD 1"),
        location_record("9999", "ZZZ", "DELETED STN", update_marker="D"),
    ]
    (directory / "RJFAF001.LOC").write_text("\n".join(location_lines) + "\n", encoding="latin-1")

    ticket_lines = [
        "/!! Start of file",
        "/!! Content type:  TTY",
        ticket_record("SOS", "STANDARD SINGLE"),
        ticket_record("SOR", "STANDARD RETURN"),
        ticket_record("GRP", "GROUPSAVE"),
        ticket_record("TKN", "INCLUSIVE TOUR"),
        ticket_record("ADV", "ADVANCE"),
        ticket_record("AD1", "ADVANCE 1ST", tkt_class="1"),
    ]
    (directory / "RJFAF001.TTY").write_text("\n".join(ticket_lines) + "\n", encoding="latin-1")

    return directory


@pytest.fixture
def cleaned(raw_feed: pathlib.Path, tmp_path: pathlib.Path) -> pathlib.Path:
    """Cleaned CSVs built from the synthetic feed."""
    output = tmp_path / "cleaned"
    records.clean_feed(raw_feed, output)
    return output


@pytest.fixture
def advance_feed(tmp_path: pathlib.Path) -> pathlib.Path:
    """A cleaned feed with an Advance ladder, for the median test."""
    directory = tmp_path / "adv-raw"
    directory.mkdir()

    flow_lines = [flow_record("0000001", LIVERPOOL_NLC, MANCHESTER_NLC)]
    # A yield-managed ladder: several price points, all described
    # ADVANCE.
    for index, pence in enumerate([900, 1_200, 1_500, 2_100, 3_000]):
        flow_lines.append(fare_record("0000001", f"D{index}0", pence))
    # First class Advance must not be counted.
    flow_lines.append(fare_record("0000001", "AD1", 9_900))
    (directory / "RJFAF002.FFL").write_text("\n".join(flow_lines) + "\n", encoding="latin-1")

    (directory / "RJFAF002.LOC").write_text(
        "\n".join(
            [
                location_record(LIVERPOOL_NLC, "LIV", "LIVERPOOL LSP"),
                location_record(MANCHESTER_NLC, "MAN", "MANCHESTER PIC"),
            ]
        )
        + "\n",
        encoding="latin-1",
    )

    ticket_lines = [ticket_record(f"D{index}0", "ADVANCE") for index in range(5)]
    ticket_lines.append(ticket_record("AD1", "ADVANCE 1ST", tkt_class="1"))
    (directory / "RJFAF002.TTY").write_text("\n".join(ticket_lines) + "\n", encoding="latin-1")

    output = tmp_path / "adv-cleaned"
    records.clean_feed(directory, output)
    return output
