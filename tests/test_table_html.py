from pathlib import Path

import docx
import pymupdf
import pytest
from docx.enum.text import WD_COLOR_INDEX
from docx.oxml import parse_xml
from docx.shared import RGBColor

from pdf2md.cli import main as pdf_main
from pdf2md.docx_cli import main as docx_main
from pdf2md.table_html import HtmlCell, Run, merge_runs, render_cell, render_table_html

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def test_run_rendering_nests_styles_and_escapes() -> None:
    cell = HtmlCell(
        paragraphs=[
            [Run("a<b", bold=True, italic=True, underline=True, strike=True, color="#ff0000")],
            [Run("x"), Run("2", superscript=True)],
        ],
        fill="#00ff00",
        colspan=2,
        rowspan=3,
    )
    html = render_cell(cell)
    assert html == (
        '<td colspan="2" rowspan="3" style="background-color:#00ff00">'
        '<span style="color:#ff0000"><b><i><u><s>a&lt;b</s></u></i></b></span>'
        "<br>x<sup>2</sup></td>"
    )


def test_merge_runs_joins_same_format_and_blanks() -> None:
    merged = merge_runs([Run("a", bold=True), Run(" "), Run("b", bold=True), Run("c")])
    assert [(r.text, r.bold) for r in merged] == [("a b", True), ("c", False)]


def test_table_html_has_no_blank_line() -> None:
    html = render_table_html([[HtmlCell([[Run("a")]])], [HtmlCell([[Run("b")]])]])
    assert "\n\n" not in html and html.count("<tr>") == 2


@pytest.fixture()
def styled_pdf(tmp_path: Path) -> Path:
    """A ruled 2x3 table: header row filled and white-on-blue, styled words in row 2."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_rect(pymupdf.Rect(60, 100, 360, 124), color=None, fill=(0.0, 0.2, 0.5))
    for row in range(3):
        page.draw_line((60, 100 + row * 24), (360, 100 + row * 24))
    page.draw_line((60, 172), (360, 172))
    for col in range(4):
        page.draw_line((60 + col * 100, 100), (60 + col * 100, 172))
    page.insert_text((68, 117), "Name", fontsize=11, fontname="hebo", color=(1, 1, 1))
    page.insert_text((168, 117), "Kind", fontsize=11, fontname="hebo", color=(1, 1, 1))
    page.insert_text((268, 117), "Note", fontsize=11, fontname="hebo", color=(1, 1, 1))
    page.insert_text((68, 141), "plain", fontsize=11)
    page.insert_text((168, 141), "slanted", fontsize=11, fontname="heit")
    page.insert_text((268, 141), "marked", fontsize=11, color=(1, 0, 0))
    page.draw_line((268, 143.5), (268 + 36, 143.5))  # underline under "marked"
    page.insert_text((68, 165), "struck", fontsize=11)
    page.draw_line((68, 161.5), (68 + 30, 161.5))  # strikethrough through "struck"
    page.insert_text((168, 165), "Bold", fontsize=11, fontname="hebo")
    path = tmp_path / "styled.pdf"
    doc.save(path)
    return path


def _convert_pdf(pdf: Path, tmp_path: Path, *extra: str) -> str:
    out = tmp_path / "out"
    code = pdf_main([str(pdf), "-o", str(out), "--no-progress", "--no-images", *extra])
    assert code == 0
    return (out / pdf.stem / f"{pdf.stem}.md").read_text(encoding="utf-8")


def test_pdf_table_keeps_fill_colour_and_text_styles(styled_pdf: Path, tmp_path: Path) -> None:
    text = _convert_pdf(styled_pdf, tmp_path, "--tables-html")
    assert text.startswith('<table border="1"')
    assert 'style="background-color:#003380"' in text
    assert '<span style="color:#ffffff"><b>Name</b></span>' in text
    assert "<i>slanted</i>" in text
    assert '<span style="color:#ff0000"><u>marked</u></span>' in text
    assert "<s>struck</s>" in text
    assert "<b>Bold</b>" in text
    assert "<u>plain</u>" not in text and "<s>plain</s>" not in text
    assert "| Name |" not in text


def test_pdf_table_without_option_stays_markdown(styled_pdf: Path, tmp_path: Path) -> None:
    text = _convert_pdf(styled_pdf, tmp_path)
    assert "<table" not in text and "| Name |" in text


def test_tables_html_and_images_are_exclusive(styled_pdf: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        pdf_main([str(styled_pdf), "-o", str(tmp_path), "--tables-html", "--tables-as-images"])
    assert info.value.code == 2


@pytest.fixture()
def styled_docx(tmp_path: Path) -> Path:
    document = docx.Document()
    document.add_paragraph("Before the table.")
    table = document.add_table(rows=3, cols=3)
    head = table.cell(0, 0).paragraphs[0]
    run = head.add_run("Head")
    run.bold = True
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    table.cell(0, 0)._tc.get_or_add_tcPr().append(
        parse_xml(f'<w:shd {W} w:val="clear" w:color="auto" w:fill="1F497D"/>')
    )
    paragraph = table.cell(0, 1).paragraphs[0]
    paragraph.add_run("it").italic = True
    paragraph.add_run(" and ")
    paragraph.add_run("under").underline = True
    paragraph.add_run(" ")
    paragraph.add_run("gone").font.strike = True
    marked = table.cell(0, 2).paragraphs[0].add_run("hi")
    marked.font.highlight_color = WD_COLOR_INDEX.YELLOW
    marked.font.superscript = True
    table.cell(1, 0).merge(table.cell(1, 1)).paragraphs[0].add_run("wide")
    table.cell(1, 2).merge(table.cell(2, 2)).paragraphs[0].add_run("tall")
    table.cell(2, 0).paragraphs[0].text = "x"
    table.cell(2, 1).paragraphs[0].text = "y"
    path = tmp_path / "styled.docx"
    document.save(str(path))
    return path


def test_docx_table_keeps_shading_spans_and_run_formats(styled_docx: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = docx_main([str(styled_docx), "-o", str(out), "--no-progress", "--tables-html"])
    assert code == 0
    text = (out / "styled" / "styled.md").read_text(encoding="utf-8")
    assert '<td style="background-color:#1f497d"><span style="color:#ffffff"><b>Head' in text
    assert "<i>it</i> and <u>under</u> <s>gone</s>" in text
    assert '<span style="background-color:#ffff00"><sup>hi</sup></span>' in text
    assert '<td colspan="2">wide</td>' in text
    assert '<td rowspan="2">tall</td>' in text
    assert text.count("<tr>") == 3 and "| Head |" not in text


@pytest.fixture()
def shaded_lines_pdf(tmp_path: Path) -> Path:
    """Borders drawn as thin filled rectangles; the shading is one rectangle per text line."""
    doc = pymupdf.open()
    page = doc.new_page()
    beige = (0.93, 0.92, 0.88)
    for y in (100, 160, 200):  # horizontal borders
        page.draw_rect(pymupdf.Rect(60, y, 360, y + 0.5), color=None, fill=(0, 0, 0))
    for x in (60, 160, 360):  # vertical borders
        page.draw_rect(pymupdf.Rect(x, 100, x + 0.5, 200.5), color=None, fill=(0, 0, 0))
    for line, y in enumerate((106, 122, 138)):  # first cell: three lines, three shaded boxes
        page.draw_rect(pymupdf.Rect(66, y, 154, y + 15), color=None, fill=beige)
        page.insert_text((68, y + 11), f"first cell line {line + 1}", fontsize=8)
    page.draw_rect(pymupdf.Rect(166, 106, 354, 154), color=None, fill=beige)
    page.insert_text((170, 120), "right", fontsize=10)
    page.insert_text((68, 180), "A", fontsize=10)
    page.insert_text((168, 180), "B", fontsize=10)
    path = tmp_path / "shaded.pdf"
    doc.save(path)
    return path


def test_cell_shaded_line_by_line_stays_one_cell(shaded_lines_pdf: Path, tmp_path: Path) -> None:
    text = _convert_pdf(shaded_lines_pdf, tmp_path, "--tables-html")
    assert text.count("<tr>") == 2
    first_row = text.split("<tr>")[1]
    assert first_row.count("<td") == 2
    assert "first cell line 1<br>first cell line 2<br>first cell line 3" in first_row
    assert first_row.count("background-color:#edebe0") == 2
