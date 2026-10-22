from pathlib import Path

import pymupdf
import pytest

from pdf2md.cli import main
from pdf2md.models import Block, Coordinates, Settings
from pdf2md.render import render_block
from pdf2md.table_images import tables_to_images

ROWS = [["A", "B"], ["1", "2"], ["3", "4"]]


@pytest.fixture()
def grid_pdf(tmp_path: Path) -> Path:
    """One page: a paragraph, a caption and a ruled 3x2 table."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 80), "Intro paragraph before the table.", fontsize=11)
    page.insert_text((60, 120), "Table 1 Letters and digits", fontsize=11)
    for row in range(4):
        page.draw_line((60, 140 + row * 24), (260, 140 + row * 24))
    for col in range(3):
        page.draw_line((60 + col * 100, 140), (60 + col * 100, 212))
    for r, cells in enumerate(ROWS):
        for c, text in enumerate(cells):
            page.insert_text((70 + c * 100, 157 + r * 24), text, fontsize=11)
    path = tmp_path / "grid.pdf"
    doc.save(path)
    return path


def _table(rows: list[list[str]] | None = None, coords: bool = True) -> Block:
    return Block(
        page_number=1,
        kind="table",
        text="A | B",
        rows=rows if rows is not None else ROWS,
        coords=Coordinates(x0=60, x1=260, top=140, bottom=212) if coords else None,
        source_pass="pass2_pdfplumber",
    )


def _doc() -> pymupdf.Document:
    doc = pymupdf.open()
    doc.new_page()
    return doc


def test_table_becomes_image_with_caption_as_alt(tmp_path: Path) -> None:
    caption = Block(
        page_number=1, kind="paragraph", text="Table 1 Letters", source_pass="pass2_pdfplumber"
    )
    settings = Settings(tables_as_images=1)
    out = tables_to_images(_doc(), [caption, _table()], tmp_path / "assets", settings)
    assert [b.kind for b in out] == ["paragraph", "image"]
    assert out[1].image_path == "assets/p0001_t01.png"
    assert out[1].text == "Table 1 Letters"
    assert out[1].coords == _table().coords
    assert (tmp_path / "assets" / "p0001_t01.png").stat().st_size > 0


def test_min_rows_and_unusable_tables_stay_text(tmp_path: Path) -> None:
    small = _table([["A", "B"]])
    no_coords = _table(coords=False)
    toc = _table().model_copy(update={"element": "toc"})
    settings = Settings(tables_as_images=3)
    out = tables_to_images(_doc(), [small, no_coords, toc], tmp_path / "assets", settings)
    assert [b.kind for b in out] == ["table", "table", "table"]
    assert not (tmp_path / "assets").exists()


def test_disabled_by_default(tmp_path: Path) -> None:
    out = tables_to_images(_doc(), [_table()], tmp_path / "assets", Settings())
    assert out[0].kind == "table"


def test_keep_text_renders_details_under_the_image(tmp_path: Path) -> None:
    settings = Settings(tables_as_images=1, tables_keep_text=True)
    block = tables_to_images(_doc(), [_table()], tmp_path / "assets", settings)[0]
    rendered = render_block(block)
    assert rendered.startswith("![Table (page 1)](assets/p0001_t01.png)")
    assert "<details>" in rendered and "| A | B |" in rendered


def test_cli_replaces_table_and_keeps_json_coordinates(grid_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = main([str(grid_pdf), "-o", str(out), "--no-progress", "--tables-as-images"])
    assert code == 0
    text = (out / "grid" / "grid.md").read_text(encoding="utf-8")
    assert "assets/p0001_t01.png" in text and "| A |" not in text
    assert (out / "grid" / "assets" / "p0001_t01.png").exists()


def test_cli_without_option_keeps_markdown_table(grid_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main([str(grid_pdf), "-o", str(out), "--no-progress"]) == 0
    assert "| A | B |" in (out / "grid" / "grid.md").read_text(encoding="utf-8")


def test_cli_refuses_no_images_combination(grid_pdf: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([str(grid_pdf), "-o", str(tmp_path), "--tables-as-images", "--no-images"])
    assert exit_info.value.code == 2
