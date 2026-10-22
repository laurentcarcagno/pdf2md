import json
from pathlib import Path

import pytest

from pdf2md import PdfOpenError, convert_pdf, write_outputs
from pdf2md.cli import main
from pdf2md.models import PageKind, Settings


def test_text_pdf_strips_margins_and_maps_paragraphs(text_pdf: Path) -> None:
    result = convert_pdf(text_pdf)
    assert result.classification.document_kind == "native_text"
    assert "ACME Confidential" not in result.markdown
    assert "/ 4" not in result.markdown
    assert "# 1. Section 1" in result.markdown
    assert "com-pound word of page 1." in result.markdown  # hyphen kept on purpose
    assert len(result.mapping) == len(result.blocks) == 12
    first = result.mapping[0]
    assert (first.page_number, first.paragraph_number_in_page, first.kind) == (1, 1, "heading")
    assert first.coordinates is not None and first.coordinates.top < first.coordinates.bottom


def test_write_outputs_roundtrip(text_pdf: Path, tmp_path: Path) -> None:
    result = convert_pdf(text_pdf)
    md_path, json_path = write_outputs(result, text_pdf, tmp_path / "out")
    assert json_path is not None
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert md_path.read_text(encoding="utf-8") == result.markdown
    assert [e["paragraph_id"] for e in data] == list(range(1, len(data) + 1))
    assert {"page_number", "snippet", "coordinates", "paragraph_number_in_page"} <= set(data[0])


def test_write_outputs_without_json(text_pdf: Path, tmp_path: Path) -> None:
    md_path, json_path = write_outputs(convert_pdf(text_pdf), text_pdf, tmp_path, write_json=False)
    assert json_path is None
    assert md_path == tmp_path / "text" / "text.md" and md_path.exists()
    assert not (tmp_path / "text" / "text.json").exists()


def test_scanned_pdf_goes_to_fallback(scanned_pdf: Path) -> None:
    result = convert_pdf(scanned_pdf, Settings(engine="ocr"))
    assert result.classification.pages[0].kind is PageKind.SCANNED
    assert result.classification.document_kind == "complex"
    assert result.blocks == []


def test_blank_page_is_skipped(blank_and_text_pdf: Path) -> None:
    result = convert_pdf(blank_and_text_pdf)
    assert result.classification.pages[0].kind is PageKind.EMPTY
    assert [b.page_number for b in result.blocks] == [2]


def test_missing_and_corrupted_files_raise(tmp_path: Path) -> None:
    with pytest.raises(PdfOpenError):
        convert_pdf(tmp_path / "missing.pdf")
    corrupted = tmp_path / "bad.pdf"
    corrupted.write_bytes(b"not a pdf at all")
    with pytest.raises(PdfOpenError):
        convert_pdf(corrupted)


def test_cli_exit_codes(text_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert main([str(text_pdf), "-o", str(out)]) == 0
    assert (out / "text" / "text.md").exists() and (out / "text" / "text.json").exists()
    assert main([str(tmp_path / "nope.pdf"), "-o", str(out)]) == 1
