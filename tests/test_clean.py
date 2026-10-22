from pdf2md.clean import find_recurring_margin_keys, mark_margins, normalize_key
from pdf2md.models import Coordinates, Line, PageContent, Settings, Table


def _line(page: int, text: str, top: float) -> Line:
    return Line(
        page_number=page,
        text=text,
        coords=Coordinates(x0=50, x1=300, top=top, bottom=top + 10),
        size=10,
        is_mono=False,
    )


def _page(number: int, footer: str) -> PageContent:
    return PageContent(
        page_number=number,
        width=595,
        height=842,
        lines=[
            _line(number, "Header", 20),
            _line(number, f"body {number}", 400),
            _line(number, footer, 800),
        ],
        tables=[],
    )


def test_normalize_key_collapses_digits_and_case() -> None:
    assert normalize_key("Page 12 / 72") == normalize_key("PAGE   3 / 72") == "page # / #"


def test_recurring_margins_are_tagged_and_body_untouched() -> None:
    pages = {n: _page(n, f"{n} / 5") for n in range(1, 6)}
    cleaned = mark_margins(pages, Settings())
    assert [(ln.text, ln.element) for ln in cleaned[2].lines] == [
        ("Header", "header"),
        ("body 2", None),
        ("2 / 5", "footer"),
    ]


def test_single_page_keeps_everything() -> None:
    pages = {1: _page(1, "1 / 1")}
    assert find_recurring_margin_keys(pages, Settings()) == {}
    assert all(ln.element is None for ln in mark_margins(pages, Settings())[1].lines)


def test_one_off_margin_line_is_kept() -> None:
    pages = {n: _page(n, f"{n} / 5") for n in range(1, 6)}
    pages[3].lines.append(_line(3, "DIFFUSION", 30))
    tagged = {ln.text: ln.element for ln in mark_margins(pages, Settings())[3].lines}
    assert tagged["DIFFUSION"] is None


def test_tall_header_label_below_the_old_band_is_still_detected() -> None:
    """A label at 100-108 pt (bottom edge past 12 % of an A4 page) recurring at that height."""
    pages = {}
    for n in range(1, 6):
        lines = [
            _line(n, "Airbus Amber", 100),
            _line(n, f"4.{n} Real section title", 120),
            _line(n, "body text", 400),
        ]
        pages[n] = PageContent(page_number=n, width=595, height=842, lines=lines, tables=[])
    tagged = {ln.text: ln.element for ln in mark_margins(pages, Settings())[3].lines}
    assert tagged == {
        "Airbus Amber": "header",
        "4.3 Real section title": None,
        "body text": None,
    }


def test_repeated_text_that_moves_between_pages_is_body() -> None:
    """Same words on every page but at different heights (e.g. a table caption): not a header."""
    pages = {}
    for n in range(1, 7):
        lines = [_line(n, "Summary of results", 150 + 40 * n), _line(n, "body", 400)]
        pages[n] = PageContent(page_number=n, width=595, height=842, lines=lines, tables=[])
    assert all(
        ln.element is None for page in mark_margins(pages, Settings()).values() for ln in page.lines
    )


def test_small_vertical_drift_is_tolerated() -> None:
    pages = {}
    for n in range(1, 6):
        lines = [_line(n, "Confidential", 30 + (n % 3)), _line(n, "body", 400)]
        pages[n] = PageContent(page_number=n, width=595, height=842, lines=lines, tables=[])
    marked = mark_margins(pages, Settings())
    assert all(page.lines[0].element == "header" for page in marked.values())


def test_shifted_header_on_one_page_is_still_tagged() -> None:
    """A page whose header sits lower (first page of a chapter) still loses the known lines."""
    pages = {}
    for n in range(1, 7):
        top = 60 if n == 4 else 30
        lines = [_line(n, "Airbus Amber", top), _line(n, "body", 400)]
        pages[n] = PageContent(page_number=n, width=595, height=842, lines=lines, tables=[])
    assert mark_margins(pages, Settings())[4].lines[0].element == "header"


def _table(page: int, top: float, bottom: float, x0: float = 340, text: str = "Ref") -> Table:
    return Table(
        page_number=page,
        coords=Coordinates(x0=x0, x1=550, top=top, bottom=bottom),
        rows=[[text, f"X{page}"], ["Date", "07/11/2025"]],
    )


def test_recurring_header_table_is_tagged_but_body_tables_are_not() -> None:
    pages = {}
    for number in range(1, 7):
        page = _page(number, f"Page {number}")
        body = _table(number, 300 + number * 20, 400 + number * 20, x0=60)
        page.tables.extend([_table(number, 40, 99, text=f"R{number}"), body])
        pages[number] = page
    marked = mark_margins(pages, Settings())
    for page in marked.values():
        assert [t.element for t in page.tables] == ["header", None]


def test_table_seen_on_one_page_only_is_kept() -> None:
    pages = {n: _page(n, f"Page {n}") for n in range(1, 7)}
    pages[2].tables.append(_table(2, 40, 99))
    assert mark_margins(pages, Settings())[2].tables[0].element is None


def test_footer_table_is_tagged_and_removed_from_blocks() -> None:
    from pdf2md.structure import build_blocks

    pages = {n: _page(n, f"Page {n}") for n in range(1, 5)}
    for page in pages.values():
        page.tables.append(_table(page.page_number, 760, 820))
    marked = mark_margins(pages, Settings())
    blocks = build_blocks(marked[1], 10.0, [], Settings(), "pass2_pdfplumber")
    assert [b.element for b in blocks if b.kind == "table"] == ["footer"]
