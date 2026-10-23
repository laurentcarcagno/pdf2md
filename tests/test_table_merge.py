import re

from pdf2md.models import Block, Coordinates, Settings
from pdf2md.table_merge import merge_split_tables

GRID = [60.0, 160.0, 260.0]


def _html(*rows: str) -> str:
    body = "\n".join(f"<tr><td>{r}</td><td>x</td></tr>" for r in rows)
    return f'<table border="1">\n{body}\n</table>'


def _table(page: int, *rows: str, grid: list[float] | None = None) -> Block:
    return Block(
        page_number=page,
        kind="table",
        text=rows[0],
        rows=[[r, "x"] for r in rows],
        coords=Coordinates(x0=60, x1=260, top=100, bottom=700),
        source_pass="pass2_pdfplumber",
        html=_html(*rows),
        grid=GRID if grid is None else grid,
    )


def _para(page: int, element: str | None = None) -> Block:
    return Block(
        page_number=page,
        kind="paragraph",
        text="p",
        source_pass="pass2_pdfplumber",
        element=element,
    )


def _first_cells(block: Block) -> list[str]:
    assert block.html
    return re.findall(r"<tr><td>(.*?)</td>", block.html)


def test_continued_table_is_merged_and_title_kept_once() -> None:
    blocks = [
        _table(3, "Title", "a", "b"),
        _para(3, "footer"),
        _para(4, "header"),
        _table(4, "Title", "c", "d"),
        _table(5, "Title", "e"),
    ]
    out = merge_split_tables(blocks, Settings())
    assert len(out) == 3 and out[0].kind == "table"
    assert _first_cells(out[0]) == ["Title", "a", "b", "c", "d", "e"]
    assert [row[0] for row in out[0].rows] == ["Title", "a", "b", "c", "d", "e"]
    assert out[0].page_number == 3 and out[0].html and out[0].html.count("</table>") == 1


def test_two_title_rows_are_dropped_together() -> None:
    out = merge_split_tables([_table(1, "T1", "T2", "a"), _table(2, "T1", "T2", "b")], Settings())
    assert _first_cells(out[0]) == ["T1", "T2", "a", "b"]


def test_no_repeated_title_keeps_every_row() -> None:
    out = merge_split_tables([_table(1, "Title", "a"), _table(2, "b", "c")], Settings())
    assert _first_cells(out[0]) == ["Title", "a", "b", "c"]


def test_tables_stay_separate_when_not_a_continuation() -> None:
    settings = Settings()
    other_grid = [60.0, 200.0, 260.0]
    assert (
        len(
            merge_split_tables(
                [_table(1, "T", "a"), _table(2, "T", "b", grid=other_grid)], settings
            )
        )
        == 2
    )
    assert len(merge_split_tables([_table(1, "T", "a"), _table(3, "T", "b")], settings)) == 2
    assert (
        len(merge_split_tables([_table(1, "T", "a"), _para(2), _table(2, "T", "b")], settings)) == 3
    )
    assert (
        len(
            merge_split_tables(
                [_table(1, "T", "a", grid=[]), _table(2, "T", "b", grid=[])], settings
            )
        )
        == 2
    )


def test_small_drift_of_the_column_borders_is_tolerated() -> None:
    drifted = [61.5, 158.0, 262.0]
    out = merge_split_tables([_table(1, "T", "a"), _table(2, "T", "b", grid=drifted)], Settings())
    assert len(out) == 1


def test_input_blocks_are_not_modified() -> None:
    first, second = _table(1, "T", "a"), _table(2, "T", "b")
    merge_split_tables([first, second], Settings())
    assert len(first.rows) == 2 and first.html == _html("T", "a")
