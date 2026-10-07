from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from rsp_fares_feed import cli
from rsp_fares_feed.cli import build_parser

if TYPE_CHECKING:
    import pathlib

    from _pytest.capture import CaptureFixture


SMOKE_ARGS = ["lookup", "LIV", "MAN"]


class TestClean:
    def test_reports_counts(
        self, raw_feed: pathlib.Path, tmp_path: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        exit_code = cli.main(["clean", str(raw_feed), "-o", str(tmp_path / "out")])
        assert exit_code == 0
        assert "locations" in capsys.readouterr().out

    def test_json_counts(
        self, raw_feed: pathlib.Path, tmp_path: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        exit_code = cli.main(["clean", str(raw_feed), "-o", str(tmp_path / "out"), "--json"])
        assert exit_code == 0
        assert json.loads(capsys.readouterr().out)["locations"] == 4

    def test_missing_directory_is_an_error(
        self, tmp_path: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["clean", str(tmp_path / "nope")]) == 1
        assert "not found" in capsys.readouterr().err


class TestLookup:
    def test_prints_fares(self, cleaned: pathlib.Path, capsys: CaptureFixture[str]) -> None:
        assert cli.main(["lookup", "LIV", "MAN", "-d", str(cleaned)]) == 0
        out = capsys.readouterr().out
        assert "STANDARD SINGLE" in out
        assert "14.5" in out

    def test_non_retail_is_hidden_and_reported(
        self, cleaned: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["lookup", "LIV", "MAN", "-d", str(cleaned)]) == 0
        captured = capsys.readouterr()
        assert "GROUPSAVE" not in captured.out
        assert "non-retail placeholders" in captured.err

    def test_non_retail_can_be_shown(
        self, cleaned: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        exit_code = cli.main(["lookup", "LIV", "MAN", "-d", str(cleaned), "--include-non-retail"])
        assert exit_code == 0
        assert "GROUPSAVE" in capsys.readouterr().out

    def test_json_output(self, cleaned: pathlib.Path, capsys: CaptureFixture[str]) -> None:
        assert cli.main(["lookup", "LIV", "MAN", "-d", str(cleaned), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["origin"] == "LIV"
        assert {fare["ticket_code"] for fare in payload["fares"]} == {"SOS", "SOR"}

    def test_csv_output(
        self, cleaned: pathlib.Path, tmp_path: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        target = tmp_path / "fares.csv"
        assert cli.main(["lookup", "LIV", "MAN", "-d", str(cleaned), "--csv", str(target)]) == 0
        assert "ticket_code" in target.read_text()

    def test_advance_median_is_reported(
        self, advance_feed: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["lookup", "LIV", "MAN", "-d", str(advance_feed)]) == 0
        assert "Median standard-class Advance: £15.00" in capsys.readouterr().out

    def test_unknown_pair_explains_clusters(
        self, cleaned: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["lookup", "AAA", "BBB", "-d", str(cleaned)]) == 0
        assert "cluster level" in capsys.readouterr().err

    def test_uncleaned_directory(
        self, tmp_path: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["lookup", "LIV", "MAN", "-d", str(tmp_path)]) == 1
        assert "rsp-fares-feed clean" in capsys.readouterr().err


class TestDownload:
    def test_missing_credentials(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("NRDP_USERNAME", raising=False)
        monkeypatch.delenv("NRDP_PASSWORD", raising=False)
        assert cli.main(["download", "-o", str(tmp_path / "raw")]) == 1
        assert "NRDP_USERNAME" in capsys.readouterr().err

    def test_there_is_no_password_flag(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(SystemExit):
            cli.main(["download", "-o", str(tmp_path), "--password", "hunter2"])


def test_no_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code != 0


@pytest.mark.parametrize(
    ("argv", "verbose"),
    [(SMOKE_ARGS, False), (["-v", *SMOKE_ARGS], True), ([*SMOKE_ARGS, "-v"], True)],
)
def test_verbose_flag(argv: list[str], verbose: bool) -> None:
    assert build_parser().parse_args(argv).verbose is verbose
