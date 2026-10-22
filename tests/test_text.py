from pdf2md.text import fix_text


def test_mojibake_and_ligatures_are_repaired() -> None:
    assert fix_text("dÃ©finition") == "définition"
    assert fix_text("ﬁnal ﬂux") == "final flux"


def test_quotes_spaces_and_newlines_untouched() -> None:
    text = "« Resume »  OBCP\nsuite"
    assert fix_text(text) == text
