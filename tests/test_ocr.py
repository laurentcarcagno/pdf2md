import shutil
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from pdf2md import convert_pdf
from pdf2md.models import PageKind, Settings
from pdf2md.ocr import OcrLine, lines_to_page, parse_tesseract_tsv, preprocess

TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "5\t1\t1\t1\t1\t1\t100\t200\t300\t40\t95.5\tHello\n"
    "5\t1\t1\t1\t1\t2\t420\t205\t200\t35\t90.0\tworld\n"
    "5\t1\t1\t1\t2\t1\t100\t260\t150\t40\t88.0\tNext\n"
    "4\t1\t1\t1\t1\t0\t100\t200\t520\t40\t-1\t\n"
)


def test_tsv_words_are_grouped_into_lines() -> None:
    lines = parse_tesseract_tsv(TSV)
    assert [line.text for line in lines] == ["Hello world", "Next"]
    assert (lines[0].x0, lines[0].x1, lines[0].y0, lines[0].y1) == (100, 620, 200, 240)


def test_pixel_lines_are_converted_to_points_and_low_confidence_dropped() -> None:
    lines = [OcrLine("keep", 300, 600, 900, 660, 90.0), OcrLine("noise", 0, 0, 10, 10, 5.0)]
    page = lines_to_page(lines, 1, 595.0, 842.0, 300)
    assert [ln.text for ln in page.lines] == ["keep"]
    assert page.lines[0].coords.x0 == pytest.approx(72.0)
    assert page.lines[0].coords.top == pytest.approx(144.0)


class _FakeEngine:
    name = "ocr_tesseract"

    def __init__(self, lines: list[OcrLine], fail: bool = False) -> None:
        self.lines, self.fail = lines, fail

    def recognize(self, image: Any) -> list[OcrLine]:
        if self.fail:
            raise RuntimeError("boom")
        return self.lines


def test_preprocess_keeps_shape() -> None:
    import numpy as np

    image = np.full((200, 300), 255, dtype=np.uint8)
    image[90:110, 50:250] = 0
    assert preprocess(image).shape == image.shape


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_scanned_page_is_read_by_tesseract(tmp_path: Path) -> None:
    source = pymupdf.open()
    page = source.new_page()
    page.insert_text((72, 120), "Specification of the service", fontsize=20)
    page.insert_text((72, 160), "The software shall report errors.", fontsize=12)
    pix = page.get_pixmap(dpi=200)
    scan = pymupdf.open()
    scan_page = scan.new_page()
    scan_page.insert_image(scan_page.rect, pixmap=pix)
    path = tmp_path / "scan.pdf"
    scan.save(path)

    result = convert_pdf(path, Settings())
    assert result.classification.pages[0].kind is PageKind.SCANNED
    assert "software shall report errors" in result.markdown
    assert all(b.source_pass.startswith("ocr_") for b in result.blocks)
    assert all(b.coords is not None for b in result.blocks)


def test_no_ocr_flag_leaves_scan_empty(tmp_path: Path) -> None:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 300), False)
    pix.clear_with(200)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, pixmap=pix)
    path = tmp_path / "scan.pdf"
    doc.save(path)
    result = convert_pdf(path, Settings(ocr_enabled=False, engine="ocr"))
    assert result.blocks == []
