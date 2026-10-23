from pdf2md.text import fix_text


def test_mojibake_and_ligatures_are_repaired() -> None:
    assert fix_text("dÃ©finition") == "définition"
    assert fix_text("ﬁnal ﬂux") == "final flux"


def test_quotes_spaces_and_newlines_untouched() -> None:
    text = "« Resume »  OBCP\nsuite"
    assert fix_text(text) == text


def test_private_use_bullets_become_real_bullets() -> None:
    assert fix_text(f"{chr(0xF0B7)} Electrical design") == "• Electrical design"
    assert fix_text(f"{chr(0xF0A7)} Item") == "▪ Item"
    assert fix_text(f"  {chr(0xF0E0)} Unknown label") == "  • Unknown label"
    assert fix_text(f"a{chr(0xF0E0)}b") == "ab"
