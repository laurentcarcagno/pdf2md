"""``--set NAME=VALUE`` reaches every tunable of Settings."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from pdf2md.cli import main
from pdf2md.models import Settings
from pdf2md.settings_cli import format_settings, parse_overrides, tunable_settings


def test_every_scalar_setting_is_tunable_with_its_bounds() -> None:
    by_name = {info.name: info for info in tunable_settings()}
    for name in ("recurrence_ratio", "margin_search_ratio", "margin_tolerance", "garbled_ratio"):
        assert name in by_name
    assert by_name["recurrence_ratio"].default == 0.4
    assert (by_name["recurrence_ratio"].minimum, by_name["recurrence_ratio"].maximum) == (0, 1)
    assert by_name["tables_html"].dedicated and not by_name["recurrence_ratio"].dedicated
    assert "keep" not in by_name  # not a scalar: it has --keep
    scalars = {
        n for n, f in Settings.model_fields.items() if f.annotation in (int, float, bool, str)
    }
    assert scalars <= set(by_name)
    assert "recurrence_ratio" in format_settings()


def test_overrides_are_validated() -> None:
    assert parse_overrides(["recurrence_ratio=0.3", "TOC_MIN_LINES=8"]) == {
        "recurrence_ratio": "0.3",
        "toc_min_lines": "8",
    }
    for bad in ("recurrence_ratio=2", "nope=1", "recurrence_ratio", "toc_min_lines=x"):
        with pytest.raises(ValueError):
            parse_overrides([bad])


def test_set_changes_the_detection_of_headers(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    document = pymupdf.open()
    for number in range(1, 9):  # a running header on 3 pages out of 8: below the default 0.4 share
        page = document.new_page()
        if number <= 3:
            page.insert_text((60, 40), "ACME recurring header", fontsize=9)
        page.insert_text((60, 120), f"Body of page {number}.", fontsize=11)
    document.save(pdf)

    def convert(*extra: str) -> str:
        out = tmp_path / ("out-" + "-".join(extra).replace("=", "_").replace("/", ""))
        assert main([str(pdf), "-o", str(out), "--no-progress", "--no-json", *extra]) == 0
        return (out / "doc" / "doc.md").read_text()

    assert "ACME recurring header" in convert()  # not recurrent enough to be removed
    assert "ACME recurring header" not in convert("--set", "recurrence_ratio=0.3")


def test_list_settings_and_bad_values_exit_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list-settings"]) == 0
    assert "margin_tolerance" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_info:
        main(["x.pdf", "--set", "recurrence_ratio=9"])
    assert exit_info.value.code == 2
