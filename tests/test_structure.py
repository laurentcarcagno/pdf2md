from pdf2md.extract import _line_from_plumber
from pdf2md.models import Coordinates, Line, PageContent, Settings
from pdf2md.structure import build_blocks, join_lines


def _line(text: str, top: float, size: float = 11, mono: bool = False) -> Line:
    return Line(
        page_number=1,
        text=text,
        coords=Coordinates(x0=50, x1=300, top=top, bottom=top + size),
        size=size,
        is_mono=mono,
    )


def test_join_lines_keeps_hyphen_of_compounds() -> None:
    assert join_lines(["pas-", "à-pas", "et mot"]) == "pas-à-pas et mot"


def test_blocks_heading_paragraph_code_and_pipe_table() -> None:
    page = PageContent(
        page_number=1,
        width=595,
        height=842,
        lines=[
            _line("1.2. Titre", 100, size=14),
            _line("ligne un", 140),
            _line("ligne deux", 157),
            _line("autre paragraphe", 220),
            _line("UID: X", 260, size=9, mono=True),
            _line("VERSION: V0", 275, size=9, mono=True),
            _line("A | B", 300),
            _line("1 | 2", 316),
        ],
        tables=[],
    )
    blocks = build_blocks(page, body_size=11, sizes=[14], settings=Settings())
    assert [(b.kind, b.text) for b in blocks] == [
        ("heading", "1.2. Titre"),
        ("paragraph", "ligne un ligne deux"),
        ("paragraph", "autre paragraphe"),
        ("code", "UID: X\nVERSION: V0"),
        ("table", "A | B\n1 | 2"),
    ]
    assert blocks[0].level == 2
    assert blocks[4].rows == [["A", "B"], ["1", "2"]]


def test_empty_page_gives_no_blocks() -> None:
    page = PageContent(page_number=1, width=595, height=842, lines=[], tables=[])
    assert build_blocks(page, 11, [], Settings()) == []


def _at(text: str, top: float, x0: float) -> Line:
    return Line(
        page_number=1,
        text=text,
        coords=Coordinates(x0=x0, x1=x0 + 200, top=top, bottom=top + 10),
        size=10,
        is_mono=False,
    )


def test_requirement_letters_notes_and_nested_lists_are_separate() -> None:
    from pdf2md.render import render_block

    page = PageContent(
        page_number=1,
        width=595,
        height=842,
        lines=[
            _at("a. First requirement text", 100, 170),
            _at("continued on a second line.", 113, 198),
            _at("NOTE A note about it", 130, 227),
            _at("that wraps.", 143, 269),
            _at("b. Second requirement", 160, 170),
            _at("c. Third shall consist of:", 177, 170),
            _at("1. one;", 193, 198),
            _at("2. two, consisting of:", 210, 198),
            _at("(a) sub one, and", 223, 227),
            _at("more sub one;", 236, 255),
            _at("(b) sub two;", 252, 227),
            _at("3. three.", 269, 198),
            _at("NOTE 1 Final note", 286, 227),
        ],
        tables=[],
    )
    blocks = build_blocks(page, body_size=10, sizes=[], settings=Settings())
    assert [(b.kind, b.text) for b in blocks] == [
        ("paragraph", "a. First requirement text continued on a second line."),
        ("paragraph", "NOTE A note about it that wraps."),
        ("paragraph", "b. Second requirement"),
        ("paragraph", "c. Third shall consist of:"),
        (
            "list",
            "1. one;\n2. two, consisting of:\n   (a) sub one, and more sub one;"
            "\n   (b) sub two;\n3. three.",
        ),
        ("paragraph", "NOTE 1 Final note"),
    ]
    assert render_block(blocks[4]) == (
        "1. one;\n2. two, consisting of:\n   - (a) sub one, and more sub one;\n"
        "   - (b) sub two;\n3. three."
    )


def test_symbol_font_bullets_make_a_list() -> None:
    raw = {"x0": 50, "x1": 300, "top": 100, "bottom": 111}
    chars = [{"size": 11, "fontname": "ArialMT"}]
    lines = [
        _line_from_plumber({**raw, "text": f"{chr(0xF0B7)} Electrical design", "chars": chars}, 1),
        _line_from_plumber({**raw, "text": f"{chr(0xF0B7)} Mechanical design", "chars": chars}, 1),
    ]
    page = PageContent(page_number=1, width=595, height=842, lines=lines, tables=[])
    blocks = build_blocks(page, body_size=11, sizes=[], settings=Settings())
    assert [b.kind for b in blocks] == ["list"]
    assert blocks[0].text == "• Electrical design\n• Mechanical design"
