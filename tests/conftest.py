"""Synthetic PDFs built with PyMuPDF so tests do not depend on external files."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest


def _write_page(page: pymupdf.Page, number: int, total: int, body: list[str]) -> None:
    page.insert_text((60, 40), "ACME Confidential Report", fontsize=9)
    page.insert_text((60, 800), f"Page {number} / {total}", fontsize=9)
    page.insert_text((60, 100), f"{number}. Section {number}", fontsize=16)
    y = 140.0
    for line in body:
        page.insert_text((60, y), line, fontsize=11)
        y += 16 if line else 40


@pytest.fixture()
def text_pdf(tmp_path: Path) -> Path:
    """Four native pages: recurring header/footer, a heading, a two-line paragraph."""
    doc = pymupdf.open()
    for number in range(1, 5):
        page = doc.new_page()
        _write_page(
            page,
            number,
            4,
            [
                f"First line of paragraph {number} that wraps with a com-",
                f"pound word of page {number}.",
                "",
                f"Second {number}.",
            ],
        )
    path = tmp_path / "text.pdf"
    doc.save(path)
    return path


@pytest.fixture()
def scanned_pdf(tmp_path: Path) -> Path:
    """One page that is only a full-page image."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 300), False)
    pix.clear_with(200)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, pixmap=pix)
    path = tmp_path / "scan.pdf"
    doc.save(path)
    return path


@pytest.fixture()
def blank_and_text_pdf(tmp_path: Path) -> Path:
    """A blank page followed by a text page."""
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page()
    page.insert_text((60, 100), "Only content here.", fontsize=11)
    path = tmp_path / "blank.pdf"
    doc.save(path)
    return path
