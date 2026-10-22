from pathlib import Path

import pytest

from pdf2md import convert_pdf
from pdf2md.models import Settings
from pdf2md.pipeline import count_selected_pages
from pdf2md.progress import PageProgress


def test_tick_counts_each_page_once() -> None:
    with PageProgress(3, enabled=False) as bar:
        for page in (1, 1, 2, 2, 2):
            bar.tick(page)
        assert bar.completed == 2


def test_count_selected_pages(text_pdf: Path) -> None:
    assert count_selected_pages(text_pdf, Settings()) == 4
    assert count_selected_pages(text_pdf, Settings(pages="2-3")) == 2
    with pytest.raises(ValueError):
        count_selected_pages(text_pdf, Settings(pages="9-"))


def test_conversion_reports_every_page(text_pdf: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="pdf2md")
    with PageProgress(4, enabled=False) as bar:
        convert_pdf(text_pdf, Settings(), progress=bar)
        assert bar.completed == 4
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "resolved 4/4 pages" in messages
    assert "removed" in messages and "converted in" in messages
