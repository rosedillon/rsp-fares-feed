from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from rsp_fares_feed import (
    build_fares_table,
    flag_non_retail,
    lookup_pair,
    median_advance_fare,
    retail_only,
)

if TYPE_CHECKING:
    import pathlib

    import pandas as pd


@pytest.fixture
def table(cleaned: pathlib.Path) -> pd.DataFrame:
    return build_fares_table(cleaned)


class TestBuildFaresTable:
    def test_nlc_codes_become_crs_codes(self, table: pd.DataFrame) -> None:
        assert "LIV" in set(table["origin"])
        assert "MAN" in set(table["destination"])

    def test_station_names_are_joined(self, table: pd.DataFrame) -> None:
        liv = table[table["origin"] == "LIV"]
        assert set(liv["origin_name"]) == {"LIVERPOOL LSP"}

    def test_ticket_descriptions_are_joined(self, table: pd.DataFrame) -> None:
        single = table[table["ticket_code"] == "SOS"]
        assert set(single["ticket_description"]) == {"STANDARD SINGLE"}

    def test_pence_are_converted_to_pounds(self, table: pd.DataFrame) -> None:
        single = table[(table["origin"] == "LIV") & (table["ticket_code"] == "SOS")]
        assert single["fare_gbp"].iloc[0] == pytest.approx(14.50)


class TestNonRetailFlagging:
    def test_sentinel_prices_are_flagged(self, table: pd.DataFrame) -> None:
        groupsave = table[table["ticket_code"] == "GRP"]
        assert groupsave["non_retail_fare"].all()

    def test_flat_token_flagged(self, table: pd.DataFrame) -> None:
        # A 1p settlement token appearing on 25 routes is not a fare.
        token = table[table["ticket_code"] == "TKN"]
        assert not token.empty
        assert token["non_retail_fare"].all()

    def test_flag_before_join(self, cleaned: pathlib.Path) -> None:
        # The flat-price rule needs to see a ticket code on every route
        # it appears on. Most of the 25 token routes have destinations
        # that do not resolve to a station, so the join drops them.
        # Flagging after the join would see one route, fall under
        # NON_RETAIL_FLAT_MIN_ROUTES and miss the token.
        import pandas as pd

        from rsp_fares_feed.records import FLOW_CSV

        flow = pd.read_csv(cleaned / FLOW_CSV, dtype=str)
        flow["fare_pence"] = pd.to_numeric(flow["fare_pence"], errors="coerce")
        token_routes_before_join = (flow["ticket_code"] == "TKN").sum()

        table = build_fares_table(cleaned)
        token_routes_after_join = (table["ticket_code"] == "TKN").sum()

        assert token_routes_before_join >= 25
        assert token_routes_after_join < token_routes_before_join
        assert flag_non_retail(flow)[flow["ticket_code"] == "TKN"].all()

    def test_genuine_fares_are_not_flagged(self, table: pd.DataFrame) -> None:
        retail = table[table["ticket_code"].isin(["SOS", "SOR"])]
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

    def test_retail_only_drops_the_flagged_rows(self, table: pd.DataFrame) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert len(retail_only(fares)) < len(fares)
        assert not retail_only(fares)["non_retail_fare"].any()


class TestLookupPair:
    def test_finds_the_forward_direction(self, table: pd.DataFrame) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert set(fares["ticket_code"]) >= {"SOS", "SOR"}

    def test_results_are_cheapest_first(self, table: pd.DataFrame) -> None:
        fares = lookup_pair(table, "LIV", "MAN")
        assert list(fares["fare_pence"]) == sorted(fares["fare_pence"])

    def test_reversible_flow(self, table: pd.DataFrame) -> None:
        # The feed only records LDS -> MAN, marked R.
        there = lookup_pair(table, "LDS", "MAN")
        back = lookup_pair(table, "MAN", "LDS")
        assert not there.empty
        assert not back.empty
        assert set(back["fare_pence"]) == set(there["fare_pence"])

    def test_reversed_row_relabelled(self, table: pd.DataFrame) -> None:
        back = lookup_pair(table, "MAN", "LDS")
        assert set(back["origin"]) == {"MAN"}
        assert set(back["destination"]) == {"LDS"}
        assert set(back["origin_name"]) == {"MANCHESTER PIC"}

    def test_single_direction_flow(self, table: pd.DataFrame) -> None:
        # LIV -> MAN is marked S, so MAN -> LIV has no fares.
        assert lookup_pair(table, "MAN", "LIV").empty

    def test_unknown_pair_is_empty_not_an_error(self, table: pd.DataFrame) -> None:
        assert lookup_pair(table, "AAA", "BBB").empty

    def test_codes_are_case_insensitive(self, table: pd.DataFrame) -> None:
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
