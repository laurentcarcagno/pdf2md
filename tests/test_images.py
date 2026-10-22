import shutil
from pathlib import Path

import pymupdf
import pytest

from pdf2md import convert_pdf
from pdf2md.cli import main
from pdf2md.images import insert_images
from pdf2md.models import Block, Coordinates, Settings


def _picture(color: tuple[int, int, int] = (200, 30, 30)) -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 160, 100), False)
    pix.clear_with(255)
    for x in range(0, 160, 8):  # stripes so the picture is not a single colour
        pix.set_rect(pymupdf.IRect(x, 0, x + 4, 100), color)
    return pix


@pytest.fixture()
def figure_pdf(tmp_path: Path) -> Path:
    """Text, a raster picture, its caption, a vector arrow diagram, a repeated logo."""
    doc = pymupdf.open()
    logo = _picture((30, 30, 200))  # PyMuPDF shares identical images: keep it distinct
    for number in range(1, 5):
        page = doc.new_page()
        page.insert_text((60, 40), "Header", fontsize=9)
        page.insert_image(pymupdf.Rect(480, 20, 540, 50), pixmap=logo)  # same logo everywhere
        page.insert_text((60, 100), f"Intro text of page {number}.", fontsize=11)
        if number == 1:
            page.insert_image(pymupdf.Rect(60, 150, 300, 300), pixmap=_picture())
            page.insert_text((60, 330), "Figure 1: Striped picture", fontsize=11)
            page.insert_text((60, 360), "Text after the picture.", fontsize=11)
        if number == 2:
            shape = page.new_shape()
            shape.draw_rect(pymupdf.Rect(60, 200, 160, 260))
            shape.draw_rect(pymupdf.Rect(300, 200, 400, 260))
            shape.draw_line(pymupdf.Point(160, 230), pymupdf.Point(300, 230))
            for tip in ((290, 222), (290, 238)):  # arrow head: diagonal segments
                shape.draw_line(pymupdf.Point(300, 230), pymupdf.Point(*tip))
            shape.draw_curve(
                pymupdf.Point(100, 280), pymupdf.Point(200, 340), pymupdf.Point(360, 280)
            )
            shape.finish(color=(0, 0, 0), fill=(0.7, 0.9, 0.9))
            shape.commit()
    path = tmp_path / "figures.pdf"
    doc.save(path)
    return path


def test_pictures_and_vector_figures_are_saved_and_inserted(
    figure_pdf: Path, tmp_path: Path
) -> None:
    images_dir = tmp_path / "out" / "figures" / "assets"
    result = convert_pdf(figure_pdf, Settings(), images_dir=images_dir)

    saved = sorted(p.name for p in images_dir.glob("*.png"))
    assert saved == ["p0001_01.png", "p0002_01.png"]  # the repeated logo is skipped
    assert "![Figure 1: Striped picture](assets/p0001_01.png)" in result.markdown
    lines = result.markdown.splitlines()
    picture = next(i for i, ln in enumerate(lines) if "p0001_01.png" in ln)
    assert any("Intro text of page 1" in ln for ln in lines[:picture])
    assert any("Text after the picture" in ln for ln in lines[picture:])
    entry = next(e for e in result.mapping if e.image_path == "assets/p0001_01.png")
    assert entry.kind == "image" and entry.page_number == 1 and entry.coordinates is not None


def test_no_images_dir_means_no_extraction(figure_pdf: Path) -> None:
    result = convert_pdf(figure_pdf, Settings())
    assert "![" not in result.markdown
    assert all(b.kind != "image" for b in result.blocks)


def test_scanned_full_page_image_is_not_a_figure(scanned_pdf: Path, tmp_path: Path) -> None:
    images_dir = tmp_path / "imgs"
    convert_pdf(scanned_pdf, Settings(engine="ocr", ocr_enabled=False), images_dir=images_dir)
    assert not images_dir.exists()


def _block(kind: str, top: float, bottom: float, text: str = "") -> Block:
    return Block(
        page_number=1,
        kind=kind,
        text=text,
        coords=Coordinates(x0=50, x1=300, top=top, bottom=bottom),
        source_pass="pass2_pdfplumber",
    )


def test_insert_images_by_position_and_table_lookalike_dropped(tmp_path: Path) -> None:
    image = _block("image", 150, 250)
    image.image_path = "d/a.png"
    lookalike = _block("image", 400, 500)
    lookalike.image_path = "d/b.png"
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "b.png").write_bytes(b"x")
    blocks = {
        1: [
            _block("paragraph", 100, 120, "before"),
            _block("paragraph", 300, 320, "Figure 2: caption below"),
            _block("table", 390, 510, "A | B"),
        ]
    }
    merged = insert_images(blocks, {1: [image, lookalike]}, tmp_path)
    kinds = [b.kind for b in merged[1]]
    assert kinds == ["paragraph", "image", "paragraph", "table"]
    assert merged[1][1].text == "Figure 2: caption below"
    assert not (tmp_path / "d" / "b.png").exists()


def test_cli_no_images_flag(figure_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main([str(figure_pdf), "-o", str(out), "--no-progress"]) == 0
    assert (out / "figures" / "assets").is_dir()
    assert "](assets/" in (out / "figures" / "figures.md").read_text(encoding="utf-8")
    other = tmp_path / "out2"
    assert main([str(figure_pdf), "-o", str(other), "--no-images", "--no-progress"]) == 0
    assert (other / "figures" / "figures.md").exists()
    assert not (other / "figures" / "assets").exists()


def test_assets_folder_is_not_created_without_images(text_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main([str(text_pdf), "-o", str(out), "--no-progress"]) == 0
    assert sorted(p.name for p in (out / "text").iterdir()) == ["text.json", "text.md"]


def _text_strip(text: str, width: int = 300) -> pymupdf.Pixmap:
    """A grayscale-looking strip with black text on white, like a label drawn as a picture."""
    source = pymupdf.open()
    page = source.new_page(width=width / 2, height=24)
    page.insert_text((4, 17), text, fontsize=12)
    return page.get_pixmap(dpi=144, alpha=False)


@pytest.fixture()
def label_pdf(tmp_path: Path) -> Path:
    """Requirement ids made of pictures side by side on a line: a repeated prefix + a number."""
    doc = pymupdf.open()
    prefix = _text_strip("HAR SSOW-", 160)
    for number in range(1, 5):
        page = doc.new_page()
        page.insert_text((60, 120), f"Body text of page {number}.", fontsize=11)
        top = 200.0
        page.insert_image(
            pymupdf.Rect(90, top, 160, top + 10), pixmap=prefix, keep_proportion=False
        )
        page.insert_image(
            pymupdf.Rect(162, top, 200, top + 10),
            pixmap=_text_strip(f"{number} /", 120),
            keep_proportion=False,
        )
        page.insert_image(
            pymupdf.Rect(202, top, 320, top + 10),
            pixmap=_text_strip(f"Title {number}", 240),
            keep_proportion=False,
        )
        page.insert_text((60, 260), "Text after the label.", fontsize=11)
    path = tmp_path / "labels.pdf"
    doc.save(path)
    return path


def test_pictures_on_one_line_become_one_image(label_pdf: Path, tmp_path: Path) -> None:
    images_dir = tmp_path / "assets"
    result = convert_pdf(label_pdf, Settings(ocr_enabled=False), images_dir=images_dir)
    images = [b for b in result.blocks if b.kind == "image"]
    assert [b.page_number for b in images] == [1, 2, 3, 4]  # the repeated prefix is kept
    first = images[0].coords
    assert first is not None and first.x0 < 95 and first.x1 > 315  # the three pieces joined
    lines = result.markdown.splitlines()
    label = next(i for i, ln in enumerate(lines) if "p0001_01.png" in ln)
    assert any("Body text of page 1" in ln for ln in lines[:label])
    assert any("Text after the label" in ln for ln in lines[label:])


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_text_picture_gets_its_text_as_alt(label_pdf: Path, tmp_path: Path) -> None:
    result = convert_pdf(label_pdf, Settings(), images_dir=tmp_path / "assets")
    alt = next(b.text for b in result.blocks if b.kind == "image" and b.page_number == 2)
    assert "SSOW" in alt and "2" in alt  # synthetic low-resolution strip: allow OCR noise
    entry = next(e for e in result.mapping if e.kind == "image" and e.page_number == 2)
    assert entry.snippet == alt


def test_repeated_strip_in_page_margin_is_still_a_logo(tmp_path: Path) -> None:
    doc = pymupdf.open()
    banner = _text_strip("ACME Confidential", 220)
    for _ in range(4):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(60, 20, 170, 32), pixmap=banner)
        page.insert_text((60, 300), "Body.", fontsize=11)
    path = tmp_path / "banner.pdf"
    doc.save(path)
    result = convert_pdf(path, Settings(ocr_enabled=False), images_dir=tmp_path / "assets")
    assert not [b for b in result.blocks if b.kind == "image"]
