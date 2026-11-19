from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rsp_fares_feed import (
    CLUSTERS_CSV,
    FaresTable,
    build_fares_table,
    flag_non_retail,
    lookup_pair,
    median_advance_fare,
    median_fare,
    parse_clusters,
    retail_only,
)

if TYPE_CHECKING:
    import pathlib


@pytest.fixture
def table(cleaned: pathlib.Path) -> FaresTable:
    return build_fares_table(cleaned)


class TestBuildFaresTable:
    def test_station_names(self, table: FaresTable) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert set(fares["origin_name"]) == {"LIVERPOOL LSP"}
        assert set(fares["destination_name"]) == {"MANCHESTER PIC"}

    def test_ticket_descriptions_are_joined(self, table: FaresTable) -> None:
        single = table.fares[table.fares["ticket_code"] == "SOS"]
        assert set(single["ticket_description"]) == {"STANDARD SINGLE"}

    def test_pence_are_converted_to_pounds(self, table: FaresTable) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert fares.loc[fares["ticket_code"] == "SOS", "fare_gbp"].iloc[0] == pytest.approx(14.50)

    def test_old_folder_without_clusters_still_loads(self, cleaned: pathlib.Path) -> None:
        (cleaned / CLUSTERS_CSV).unlink()
        fares = lookup_pair(build_fares_table(cleaned), "LIV", "MAN")
        assert "OPS" not in set(fares["ticket_code"])
        assert "SOS" in set(fares["ticket_code"])


class TestClusters:
    def test_cluster_fare_is_found(self, table: FaresTable) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert 1_100 in set(fares.loc[fares["ticket_code"] == "OPS", "fare_pence"])

    def test_group_fare_is_found(self, table: FaresTable) -> None:
        fares = lookup_pair(table, "LIV", "LDS")
        assert set(fares["fare_pence"]) == {2_000}

    def test_parse_clusters_reads_both_sources(self, raw_feed: pathlib.Path) -> None:
        pairs = set(parse_clusters(raw_feed).itertuples(index=False, name=None))
        assert pairs == {("Q814", "2968"), ("1072", "8446")}


class TestNonRetailFlagging:
    def test_sentinel_prices_are_flagged(self, table: FaresTable) -> None:
        groupsave = table.fares[table.fares["ticket_code"] == "GRP"]
        assert groupsave["non_retail_fare"].all()

    def test_flat_token_flagged(self, table: FaresTable) -> None:
        # A 1p settlement token on 25 routes is not a fare. Most of
        # those routes end at codes that are not stations, so the flag
        # has to be worked out before any lookup.
        token = table.fares[table.fares["ticket_code"] == "TKN"]
        assert len(token) == 25
        assert token["non_retail_fare"].all()

    def test_genuine_fares_are_not_flagged(self, table: FaresTable) -> None:
        retail = table.fares[table.fares["ticket_code"].isin(["SOS", "SOR"])]
        assert not retail["non_retail_fare"].any()

    def test_cheap_fare_on_few_routes_kept(self, cleaned: pathlib.Path) -> None:
        # The flat-price rule needs many routes; a genuinely cheap fare
        # on a single route must survive.
        import pandas as pd

        frame = pd.DataFrame(
            {
                "ticket_code": ["CHP"] * 3 + ["SOS"] * 3,
                "fare_pence": [10, 10, 10, 1450, 1500, 1600],
            }
        )
        assert not flag_non_retail(frame).any()

    def test_retail_only_drops_the_flagged_rows(self, table: FaresTable) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert len(retail_only(fares)) < len(fares)
        assert not retail_only(fares)["non_retail_fare"].any()


class TestLookupPair:
    def test_finds_the_forward_direction(self, table: FaresTable) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert set(fares["ticket_code"]) >= {"SOS", "SOR"}

    def test_results_are_cheapest_first(self, table: FaresTable) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert list(fares["fare_pence"]) == sorted(fares["fare_pence"])

    def test_reversible_flow(self, table: FaresTable) -> None:
        # The feed only records LDS -> MAN, marked R.
        there = lookup_pair(table, "LDS", "MAN")
        back = lookup_pair(table, "MAN", "LDS")
        assert not there.empty
        assert not back.empty
        assert set(back["fare_pence"]) == set(there["fare_pence"])

    def test_reversed_row_relabelled(self, table: FaresTable) -> None:
        back = lookup_pair(table, "MAN", "LDS")
        assert set(back["origin"]) == {"MAN"}
        assert set(back["destination"]) == {"LDS"}
        assert set(back["origin_name"]) == {"MANCHESTER PIC"}

    def test_single_direction_flow(self, table: FaresTable) -> None:
        # LIV -> MAN is marked S, so MAN -> LIV has no fares.
        assert lookup_pair(table, "MAN", "LIV").empty

    def test_unknown_pair_is_empty_not_an_error(self, table: FaresTable) -> None:
        assert lookup_pair(table, "AAA", "BBB").empty

    def test_codes_are_case_insensitive(self, table: FaresTable) -> None:
        assert len(lookup_pair(table, "liv", " man ")) == len(lookup_pair(table, "LIV", "MAN"))


class TestMedianAdvanceFare:
    def test_median_across_the_ladder(self, advance_feed: pathlib.Path) -> None:
        table = build_fares_table(advance_feed)
        # Ladder is 9.00, 12.00, 15.00, 21.00, 30.00 -> median 15.00.
        assert median_advance_fare(table, "LIV", "MAN") == pytest.approx(15.00)

    def test_first_class_advance_is_excluded(self, advance_feed: pathlib.Path) -> None:
        table = build_fares_table(advance_feed)
        # The 99.00 first-class fare would pull the median up if
        # counted.
        assert median_advance_fare(table, "LIV", "MAN") == pytest.approx(15.00)

    def test_no_advance_fare(self, cleaned: pathlib.Path) -> None:
        table = build_fares_table(cleaned)
        assert median_advance_fare(table, "LIV", "MAN") is None

    def test_unknown_pair(self, advance_feed: pathlib.Path) -> None:
        table = build_fares_table(advance_feed)
        assert median_advance_fare(table, "AAA", "BBB") is None


class TestMedianFare:
    def test_singles_and_returns_per_journey(self, table: FaresTable) -> None:
        # LIV -> MAN: OPS 11.00 single only; SOS and SOR are not
        # ordinary adult products by description.
        assert median_fare(table, "LIV", "MAN") == pytest.approx(11.00)

    def test_advance_ladder_counts(self, advance_feed: pathlib.Path) -> None:
        assert median_fare(build_fares_table(advance_feed), "LIV", "MAN") == pytest.approx(15.00)

    def test_unknown_pair(self, table: FaresTable) -> None:
        assert median_fare(table, "AAA", "BBB") is None
