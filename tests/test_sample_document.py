import json
from pathlib import Path

import pymupdf
import pytest

from pdf2md import convert_pdf, write_outputs

SAMPLES = sorted((Path(__file__).parent.parent / "test_inputs").glob("*.pdf"))


@pytest.mark.skipif(not SAMPLES, reason="no sample PDF in test_inputs/")
def test_real_document(tmp_path: Path) -> None:
    pdf = SAMPLES[0]
    result = convert_pdf(pdf)
    with pymupdf.open(pdf) as doc:
        page_count = doc.page_count
        sizes = {n + 1: doc[n].rect for n in range(page_count)}
    assert "ISIS documentation" not in result.markdown
    assert not any(
        line.strip().endswith(f"/ {page_count}") for line in result.markdown.splitlines()
    )
    assert len(result.mapping) == len(result.blocks) > 0
    for entry in result.mapping:
        assert 1 <= entry.page_number <= page_count
        box = entry.coordinates
        assert box is not None
        rect = sizes[entry.page_number]
        assert 0 <= box.x0 <= box.x1 <= rect.width + 1
        assert 0 <= box.top <= box.bottom <= rect.height + 1
    _, json_path = write_outputs(result, pdf, tmp_path)
    assert json_path is not None
    assert len(json.loads(json_path.read_text(encoding="utf-8"))) == len(result.mapping)
