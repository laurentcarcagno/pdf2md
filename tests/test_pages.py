from pathlib import Path

import pytest

from pdf2md import convert_pdf
from pdf2md.models import Settings
from pdf2md.pages import parse_page_spec, resolve_pages


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("10-", [(10, None)]),
        ("-10", [(1, 10)]),
        ("15-18", [(15, 18)]),
        ("7", [(7, 7)]),
        ("[1-5,10-20, 30-]", [(1, 5), (10, 20), (30, None)]),
    ],
)
def test_parse_valid_specs(spec: str, expected: list[tuple[int, int | None]]) -> None:
    assert parse_page_spec(spec) == expected


@pytest.mark.parametrize("spec", ["", "[]", "-", "a-b", "5-3", "0-4", "1,,2", "1-2-3", "-0"])
def test_parse_invalid_specs(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_page_spec(spec)


def test_resolve_merges_overlaps_and_clamps() -> None:
    assert resolve_pages("1-3,2-5,8-", 10) == [1, 2, 3, 4, 5, 8, 9, 10]
    assert resolve_pages("-100", 4) == [1, 2, 3, 4]
    assert resolve_pages("2,50-", 4) == [2]


def test_resolve_rejects_selection_outside_document() -> None:
    with pytest.raises(ValueError):
        resolve_pages("20-", 4)


def test_settings_validates_pages() -> None:
    with pytest.raises(ValueError):
        Settings(pages="9-2")


def test_convert_only_selected_pages(text_pdf: Path) -> None:
    result = convert_pdf(text_pdf, Settings(pages="2,4-"))
    assert sorted({b.page_number for b in result.blocks}) == [2, 4]
    assert [p.page_number for p in result.classification.pages] == [2, 4]
