from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rsp_fares_feed import (
    FLOW_CSV,
    LOCATIONS_CSV,
    TICKETS_CSV,
    FeedFileMissingError,
    clean_feed,
    cleaned_files_exist,
    find_feed_file,
    parse_flow_file,
    parse_locations_file,
    parse_ticket_types_file,
)

if TYPE_CHECKING:
    import pathlib


class TestFindFeedFile:
    def test_finds_by_extension(self, raw_feed: pathlib.Path) -> None:
        assert find_feed_file(raw_feed, ".FFL").name == "RJFAF001.FFL"

    def test_missing_file_explains_the_naming(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(FeedFileMissingError, match="RJFAF882"):
            find_feed_file(tmp_path, ".FFL")


class TestParseFlowFile:
    def test_fares_join_to_their_flow(self, raw_feed: pathlib.Path) -> None:
        flow = parse_flow_file(find_feed_file(raw_feed, ".FFL"))
        liv_man = flow[(flow["origin_code"] == "1444") & (flow["destination_code"] == "2968")]
        assert set(liv_man["ticket_code"]) == {"SOS", "SOR", "GRP"}

    def test_fare_pence_is_numeric(self, raw_feed: pathlib.Path) -> None:
        flow = parse_flow_file(find_feed_file(raw_feed, ".FFL"))
        single = flow[(flow["ticket_code"] == "SOS") & (flow["origin_code"] == "1444")]
        assert single["fare_pence"].iloc[0] == 1450

    def test_comment_lines_are_skipped(self, raw_feed: pathlib.Path) -> None:
        flow = parse_flow_file(find_feed_file(raw_feed, ".FFL"))
        assert not flow["origin_code"].str.startswith("!").any()

    def test_deleted_flows_are_dropped(self, raw_feed: pathlib.Path) -> None:
        flow = parse_flow_file(find_feed_file(raw_feed, ".FFL"))
        # Flow 0000003 (LIV->LDS) carried a D marker.
        pairs = set(zip(flow["origin_code"], flow["destination_code"], strict=True))
        assert ("1444", "8446") not in pairs

    def test_deleted_fares_are_dropped(self, raw_feed: pathlib.Path) -> None:
        flow = parse_flow_file(find_feed_file(raw_feed, ".FFL"))
        assert "XXX" not in set(flow["ticket_code"])

    def test_fare_without_flow_dropped(self, raw_feed: pathlib.Path) -> None:
        # The inner join is what drops it; without a flow there is no
        # route.
        flow = parse_flow_file(find_feed_file(raw_feed, ".FFL"))
        assert 700 not in set(flow["fare_pence"])

    def test_direction_is_preserved(self, raw_feed: pathlib.Path) -> None:
        flow = parse_flow_file(find_feed_file(raw_feed, ".FFL"))
        leeds = flow[flow["origin_code"] == "8446"]
        assert set(leeds["direction"]) == {"R"}

    def test_empty_file(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "empty.FFL"
        path.write_text("/!! nothing here\n")
        frame = parse_flow_file(path)
        assert frame.empty
        assert "fare_pence" in frame.columns


class TestParseLocationsFile:
    def test_stations_resolve_nlc_to_crs(self, raw_feed: pathlib.Path) -> None:
        locations = parse_locations_file(find_feed_file(raw_feed, ".LOC"))
        lookup = dict(zip(locations["nlc_code"], locations["crs_code"], strict=True))
        assert lookup["1444"] == "LIV"
        assert lookup["2968"] == "MAN"

    def test_names_are_read(self, raw_feed: pathlib.Path) -> None:
        locations = parse_locations_file(find_feed_file(raw_feed, ".LOC"))
        names = dict(zip(locations["nlc_code"], locations["description"], strict=True))
        assert names["8446"] == "LEEDS"

    def test_entries_without_a_crs_are_dropped(self, raw_feed: pathlib.Path) -> None:
        # Travelcard zones carry an NLC but are not stations.
        locations = parse_locations_file(find_feed_file(raw_feed, ".LOC"))
        assert "0027" not in set(locations["nlc_code"])

    def test_deleted_locations_are_dropped(self, raw_feed: pathlib.Path) -> None:
        locations = parse_locations_file(find_feed_file(raw_feed, ".LOC"))
        assert "ZZZ" not in set(locations["crs_code"])


class TestParseTicketTypesFile:
    def test_descriptions_are_read(self, raw_feed: pathlib.Path) -> None:
        tickets = parse_ticket_types_file(find_feed_file(raw_feed, ".TTY"))
        lookup = dict(zip(tickets["ticket_code"], tickets["description"], strict=True))
        assert lookup["SOS"] == "STANDARD SINGLE"

    def test_class_is_read(self, raw_feed: pathlib.Path) -> None:
        tickets = parse_ticket_types_file(find_feed_file(raw_feed, ".TTY"))
        classes = dict(zip(tickets["ticket_code"], tickets["tkt_class"], strict=True))
        assert classes["SOS"] == "2"
        assert classes["AD1"] == "1"


class TestCleanFeed:
    def test_writes_three_csvs(self, cleaned: pathlib.Path) -> None:
        for name in (FLOW_CSV, LOCATIONS_CSV, TICKETS_CSV):
            assert (cleaned / name).exists()
        assert cleaned_files_exist(cleaned)

    def test_counts_are_returned(self, raw_feed: pathlib.Path, tmp_path: pathlib.Path) -> None:
        counts = clean_feed(raw_feed, tmp_path / "out")
        assert counts["locations"] == 4
        assert counts["tickets"] == 6
        assert counts["flow"] > 0

    def test_missing_cleaned_dir_is_detected(self, tmp_path: pathlib.Path) -> None:
        assert not cleaned_files_exist(tmp_path)
