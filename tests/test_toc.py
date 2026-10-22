from pdf2md.filters import apply_keep
from pdf2md.models import Block
from pdf2md.toc import is_entry_line, mark_contents, strip_leaders


def _block(text: str, kind: str = "paragraph", page: int = 1) -> Block:
    return Block(page_number=page, kind=kind, text=text, source_pass="pass2_pdfplumber")


def test_entry_line_needs_more_than_a_trailing_number() -> None:
    assert is_entry_line("4.8 Mode pas-à-pas ........ 39")
    assert is_entry_line("4.8.1 Titre 40")
    assert is_entry_line("Figure 3-1: Architecture 12")
    assert not is_entry_line("The value is 12")
    assert not is_entry_line("Foreword")


def test_strip_leaders() -> None:
    assert strip_leaders("Foreword ........ 5") == "Foreword 5"


def test_titled_sections_are_marked_across_pages() -> None:
    blocks = [
        _block("Table of contents", "heading"),
        _block("1 Scope ...... 7\n2 Terms ..... 9", "toc"),
        _block("3 Requirements ...... 12", page=2),
        _block("List of figures", "heading", page=2),
        _block("Figure 1: Overview ..... 8", page=2),
        _block("Liste des tableaux", "heading", page=2),
        _block("No table of figures entries found.", page=2),
        _block("1 Scope", "heading", page=3),
        _block("Body text of the scope.", page=3),
    ]
    elements = [b.element for b in mark_contents(blocks)]
    assert elements == [
        "toc",
        "toc",
        "toc",
        "list_of_figures",
        "list_of_figures",
        "list_of_tables",
        "list_of_tables",
        None,
        None,
    ]


def test_body_section_named_contents_of_the_box_is_not_a_toc() -> None:
    blocks = [_block("1. Contents of the box", "heading"), _block("A screwdriver and 2 bolts.")]
    assert [b.element for b in mark_contents(blocks)] == [None, None]


def test_untitled_toc_run_is_still_tagged() -> None:
    assert mark_contents([_block("1 A ... 3\n2 B ... 4", "toc")])[0].element == "toc"


def test_apply_keep_removes_unlisted_elements_only() -> None:
    blocks = mark_contents([_block("Sommaire", "heading"), _block("1 A ... 3"), _block("Corps")])
    assert [b.text for b in apply_keep(blocks, frozenset())] == ["Corps"]
    assert len(apply_keep(blocks, frozenset({"toc"}))) == 3
    assert len(apply_keep(blocks, frozenset({"header"}))) == 1
