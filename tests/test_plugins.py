import json
from pathlib import Path

import pytest

from pdf2md.cli import main as pdf_main
from pdf2md.docx_cli import main as docx_main
from pdf2md.plugins import (
    PluginError,
    default_plugin_dir,
    list_plugins,
    load_plugin,
    post_process_outputs,
    register_plugin,
)

UPPER = "def process(markdown, context):\n    return markdown.upper()\n"
EXCLAIM = 'def process(markdown, context):\n    return markdown + "!"\n'


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / "scripts" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_register_copies_script_and_records_name(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    copy = register_plugin("shout it", _script(tmp_path, "upper.py", UPPER), plugin_dir)
    assert copy.parent == plugin_dir and copy.read_text(encoding="utf-8") == UPPER
    assert list_plugins(plugin_dir) == {"shout it": copy}
    registry = json.loads((plugin_dir / "registry.json").read_text(encoding="utf-8"))
    assert registry == {"shout it": copy.name}


def test_registering_a_name_again_replaces_its_script(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    first = register_plugin("p", _script(tmp_path, "a.py", UPPER), plugin_dir)
    second = register_plugin("p", _script(tmp_path, "b.py", EXCLAIM), plugin_dir)
    assert first == second and "!" in second.read_text(encoding="utf-8")
    assert len(list_plugins(plugin_dir)) == 1


def test_names_with_the_same_slug_get_distinct_files(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    one = register_plugin("a b", _script(tmp_path, "a.py", UPPER), plugin_dir)
    two = register_plugin("a/b", _script(tmp_path, "b.py", EXCLAIM), plugin_dir)
    assert one != two and one.exists() and two.exists()


@pytest.mark.parametrize(
    ("name", "file_name", "body", "message"),
    [
        ("", "ok.py", UPPER, "plugin name"),
        ("x", "nope.txt", UPPER, "not a .py file"),
        ("x", "bad.py", "def process(:\n", "cannot be parsed"),
        ("x", "noentry.py", "def other():\n    pass\n", "must define a function process"),
    ],
)
def test_invalid_registrations_are_refused(
    tmp_path: Path, name: str, file_name: str, body: str, message: str
) -> None:
    with pytest.raises(PluginError, match=message):
        register_plugin(name, _script(tmp_path, file_name, body), tmp_path / "plugins")
    assert not (tmp_path / "plugins" / "registry.json").exists()


def test_missing_script_and_unknown_plugin(tmp_path: Path) -> None:
    with pytest.raises(PluginError, match="not a .py file"):
        register_plugin("x", tmp_path / "missing.py", tmp_path / "plugins")
    with pytest.raises(PluginError, match="unknown plugin 'ghost'"):
        load_plugin("ghost", tmp_path / "plugins")


def test_script_that_crashes_on_import_is_reported(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    register_plugin(
        "boom",
        _script(tmp_path, "boom.py", "raise ValueError('x')\ndef process(m, c):\n    return m\n"),
        plugin_dir,
    )
    with pytest.raises(PluginError, match="importing .* failed"):
        load_plugin("boom", plugin_dir)


def test_default_plugin_dir_honours_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PDF2MD_PLUGIN_DIR", str(tmp_path / "custom"))
    assert default_plugin_dir() == tmp_path / "custom"
    monkeypatch.delenv("PDF2MD_PLUGIN_DIR")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert default_plugin_dir() == tmp_path / "xdg" / "pdf2md" / "plugins"


def _output(tmp_path: Path) -> tuple[Path, Path]:
    folder = tmp_path / "out" / "doc"
    folder.mkdir(parents=True)
    md, js = folder / "doc.md", folder / "doc.json"
    md.write_text("hello\n", encoding="utf-8")
    js.write_text("[]", encoding="utf-8")
    return md, js


def test_plugins_run_in_the_order_given(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    register_plugin("upper", _script(tmp_path, "upper.py", UPPER), plugin_dir)
    register_plugin("bang", _script(tmp_path, "bang.py", EXCLAIM), plugin_dir)
    md, js = _output(tmp_path)
    plugins = [load_plugin(n, plugin_dir) for n in ("bang", "upper", "bang")]
    post_process_outputs(plugins, tmp_path / "doc.pdf", md, js, md.parent / "assets")
    assert md.read_text(encoding="utf-8") == "HELLO\n!!"  # (hello + !) -> upper -> + !


def test_plugin_can_rename_output_files(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    rename = (
        "from pdf2md.plugins import PluginResult\n"
        "def process(markdown, context):\n"
        "    return PluginResult(markdown=markdown + context.plugin_name, stem='final')\n"
    )
    register_plugin("rename", _script(tmp_path, "rename.py", rename), plugin_dir)
    md, js = _output(tmp_path)
    new_md, new_js = post_process_outputs(
        [load_plugin("rename", plugin_dir)], tmp_path / "doc.pdf", md, js, md.parent / "assets"
    )
    assert (new_md.name, new_js.name if new_js else None) == ("final.md", "final.json")
    assert not md.exists() and not js.exists()
    assert new_md.read_text(encoding="utf-8") == "hello\nrename"


@pytest.mark.parametrize(
    "body",
    [
        "def process(m, c):\n    raise RuntimeError('nope')\n",
        "def process(m, c):\n    return 42\n",
        "from pdf2md.plugins import PluginResult\ndef process(m, c):\n"
        "    return PluginResult(markdown=m, stem='../escape')\n",
    ],
)
def test_failing_plugin_leaves_previous_result(tmp_path: Path, body: str) -> None:
    plugin_dir = tmp_path / "plugins"
    register_plugin("ok", _script(tmp_path, "ok.py", EXCLAIM), plugin_dir)
    register_plugin("bad", _script(tmp_path, "bad.py", body), plugin_dir)
    md, js = _output(tmp_path)
    plugins = [load_plugin("ok", plugin_dir), load_plugin("bad", plugin_dir)]
    with pytest.raises(PluginError):
        post_process_outputs(plugins, tmp_path / "doc.pdf", md, js, md.parent / "assets")
    assert md.read_text(encoding="utf-8").startswith("hello")
    assert md.exists()


def test_rename_refuses_to_overwrite(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    body = (
        "from pdf2md.plugins import PluginResult\n"
        "def process(m, c):\n    return PluginResult(m, 'taken')\n"
    )
    register_plugin("r", _script(tmp_path, "r.py", body), plugin_dir)
    md, js = _output(tmp_path)
    (md.parent / "taken.md").write_text("other", encoding="utf-8")
    with pytest.raises(PluginError, match="already exists"):
        post_process_outputs(
            [load_plugin("r", plugin_dir)], tmp_path / "d.pdf", md, js, md.parent / "assets"
        )
    assert (md.parent / "taken.md").read_text(encoding="utf-8") == "other"


def test_pdf_cli_register_then_post_process(text_pdf: Path, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    script = _script(
        tmp_path,
        "strip.py",
        "def process(markdown, context):\n    return markdown.replace('Second', 'Deuxieme')\n",
    )
    common = ["--plugin-dir", str(plugin_dir)]
    assert pdf_main(["--register-plugin", "fr", str(script), *common]) == 0  # no input: exits
    out = tmp_path / "out"
    code = pdf_main(
        [str(text_pdf), "-o", str(out), "--no-progress", "--post-process", "fr", *common]
    )
    assert code == 0
    text = (out / "text" / "text.md").read_text(encoding="utf-8")
    assert "Deuxieme 1." in text and "Second " not in text


def test_pdf_cli_unknown_plugin_fails_before_converting(text_pdf: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = pdf_main(
        [
            str(text_pdf),
            "-o",
            str(out),
            "--post-process",
            "ghost",
            "--plugin-dir",
            str(tmp_path / "p"),
        ]
    )
    assert code == 2 and not out.exists()


def test_cli_plugin_failure_is_reported_in_exit_code(text_pdf: Path, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    register_plugin(
        "bad",
        _script(tmp_path, "bad.py", "def process(m, c):\n    raise KeyError('x')\n"),
        plugin_dir,
    )
    out = tmp_path / "out"
    code = pdf_main(
        [
            str(text_pdf),
            "-o",
            str(out),
            "--no-progress",
            "--post-process",
            "bad",
            "--plugin-dir",
            str(plugin_dir),
        ]
    )
    assert code == 1 and (out / "text" / "text.md").exists()  # unmodified output is kept


def test_list_plugins_and_missing_input(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    plugin_dir = tmp_path / "plugins"
    register_plugin("shout", _script(tmp_path, "u.py", UPPER), plugin_dir)
    caplog.set_level("INFO")
    assert pdf_main(["--list-plugins", "--plugin-dir", str(plugin_dir)]) == 0
    assert "shout" in caplog.text
    with pytest.raises(SystemExit):
        pdf_main(["--plugin-dir", str(plugin_dir)])  # nothing to do


def test_docx_cli_supports_plugins(tmp_path: Path) -> None:
    import docx

    document = docx.Document()
    document.add_paragraph("plain text")
    source = tmp_path / "sample.docx"
    document.save(str(source))
    plugin_dir = tmp_path / "plugins"
    register_plugin("shout", _script(tmp_path, "u.py", UPPER), plugin_dir)
    out = tmp_path / "out"
    code = docx_main(
        [
            str(source),
            "-o",
            str(out),
            "--no-progress",
            "--post-process",
            "shout",
            "--plugin-dir",
            str(plugin_dir),
        ]
    )
    assert code == 0
    assert (out / "sample" / "sample.md").read_text(encoding="utf-8").strip() == "PLAIN TEXT"


def test_shipped_example_plugins(tmp_path: Path) -> None:
    examples = Path(__file__).parent.parent / "examples" / "plugins"
    plugin_dir = tmp_path / "plugins"
    register_plugin("remove notes", examples / "remove_notes.py", plugin_dir)
    register_plugin("prefix", examples / "rename_with_prefix.py", plugin_dir)
    md, js = _output(tmp_path)
    md.write_text("Intro.\n\nNOTE 1 drop me\n\nKept paragraph.\n", encoding="utf-8")
    new_md, _ = post_process_outputs(
        [load_plugin("remove notes", plugin_dir), load_plugin("prefix", plugin_dir)],
        tmp_path / "doc.pdf",
        md,
        js,
        md.parent / "assets",
    )
    assert new_md.name == "converted_doc.md"
    assert new_md.read_text(encoding="utf-8") == "Intro.\n\nKept paragraph.\n"


def _png(path: Path, width: int, height: int) -> None:
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(120)
    pix.save(path)


def test_ads_plugin_turns_title_strips_into_text(tmp_path: Path) -> None:
    examples = Path(__file__).parent.parent / "examples" / "plugins"
    plugin_dir = tmp_path / "plugins"
    register_plugin("ADS", examples / "ads.py", plugin_dir)
    md, js = _output(tmp_path)
    assets = md.parent / "assets"
    assets.mkdir()
    _png(assets / "strip.png", 400, 40)
    _png(assets / "shared.png", 400, 40)
    _png(assets / "figure.png", 400, 300)
    _png(assets / "generic.png", 400, 40)
    md.write_text(
        "![HAR SSOW-1 / Inputs](assets/strip.png)\n\nBody text.\n\n"
        "![Figure 1](assets/figure.png)\n\n![Image (page 2)](assets/generic.png)\n\n"
        "![Shared title](assets/shared.png)\n\nSee ![inline](assets/shared.png) here.\n",
        encoding="utf-8",
    )
    new_md, _ = post_process_outputs(
        [load_plugin("ADS", plugin_dir)], tmp_path / "d.pdf", md, js, assets
    )
    text = new_md.read_text(encoding="utf-8")
    assert text.startswith('<a id="har-ssow-1"></a>**HAR SSOW-1 / Inputs**\n\nBody text.')
    assert "![Figure 1](assets/figure.png)" in text
    assert "![Image (page 2)](assets/generic.png)" in text
    assert '\n\n<a id="shared-title"></a>**Shared title**\n\n' in text
    assert not (assets / "strip.png").exists()
    assert (assets / "figure.png").exists()
    assert (assets / "generic.png").exists()
    assert (assets / "shared.png").exists()


REQUIREMENTS = (
    "3.1 Wiring\n\n"
    "#EUR-DCH-000010 Interconnection wiring Parent(s): none Assignment: Verification: [R, I] "
    "The DC harness shall be manufactured as per [AD09]. ##\n\n"
    "#EUR-DCH-000020 Harness continuity Parent(s): EUR-EMC-REQ-000460 Assignment: Verification: "
    "[T] Continuity shall be measured.\n\nSecond paragraph.\n\n##\n\n"
    "Plain paragraph kept as is.\n\n"
    "#EUR-DCH-000030 Unclosed Parent(s): none Assignment: Verification: [R] Never closed.\n\n"
    "### END OF DOCUMENT\n"
)


def _run_tas(tmp_path: Path, markdown: str) -> str:
    examples = Path(__file__).parent.parent / "examples" / "plugins"
    plugin_dir = tmp_path / "plugins"
    register_plugin("Thales Alénia Space", examples / "thales_alenia_space.py", plugin_dir)
    md, js = _output(tmp_path)
    md.write_text(markdown, encoding="utf-8")
    new_md, _ = post_process_outputs(
        [load_plugin("Thales Alénia Space", plugin_dir)], tmp_path / "d.pdf", md, js, md.parent
    )
    return new_md.read_text(encoding="utf-8")


def test_thales_plugin_marks_requirements(tmp_path: Path) -> None:
    text = _run_tas(tmp_path, REQUIREMENTS)
    first = (
        '<a id="eur-dch-000010"></a>**EUR-DCH-000010 Interconnection wiring**\n\n'
        "- Parent(s): none\n- Assignment:\n- Verification: [R, I]\n\n"
        "The DC harness shall be manufactured as per [AD09].\n\n"
        "<!-- end EUR-DCH-000010 -->"
    )
    assert first in text
    assert "- Parent(s): EUR-EMC-REQ-000460" in text
    assert (
        "Continuity shall be measured.\n\nSecond paragraph.\n\n<!-- end EUR-DCH-000020 -->" in text
    )
    assert "\n\nPlain paragraph kept as is.\n\n" in text
    assert "##\n" not in text.replace("### END", "")


def test_thales_plugin_unclosed_requirement_stops_at_heading(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    text = _run_tas(tmp_path, REQUIREMENTS)
    assert "Never closed.\n\n<!-- end EUR-DCH-000030 -->\n\n### END OF DOCUMENT" in text
    assert "no closing ##" in caplog.text


def test_thales_plugin_title_on_its_own_line_and_no_requirement(tmp_path: Path) -> None:
    text = _run_tas(tmp_path, "#EUR-DCH-000001 Title line\nBody line. ##\n\n# Heading\n\n#tag\n")
    assert text.startswith('<a id="eur-dch-000001"></a>**EUR-DCH-000001 Title line**\n\nBody line.')
    assert "# Heading\n\n#tag" in text
    assert _run_tas(tmp_path / "again", "Nothing here.\n") == "Nothing here.\n"


@pytest.mark.parametrize(
    "furniture",
    [
        "CONTROLLATO NON CLASSIFICATO THALES ALENIA SPACE ITALIA",
        "REFERENCE: SPE-EUR-S-0009-TASI",
        "DATE: 07/11/2025",
        "ISSUE: 1 Page: 9/31",
        "REFERENCE: DATE: SPE-EUR-S-0009-TASI 07/11/2025 ISSUE: 1 Page: 31/31",
    ],
)
def test_thales_plugin_removes_page_header_and_footer(tmp_path: Path, furniture: str) -> None:
    markdown = (
        f"Before.\n\n{furniture}\n\n#EUR-DCH-000001 T Parent(s): none Assignment: "
        f"Verification: [R] Body.\n\n{furniture}\n\nShall stay. ##\n\nAfter."
    )
    text = _run_tas(tmp_path, markdown)
    assert furniture not in text
    assert "Body.\n\nShall stay.\n\n<!-- end EUR-DCH-000001 -->\n\nAfter." in text


def test_thales_plugin_keeps_normal_text_mentioning_the_labels(tmp_path: Path) -> None:
    kept = "1.3 REFERENCE DOCUMENTS\n\nThe ISSUE: of the date was discussed.\n\nPage: see below"
    assert _run_tas(tmp_path, kept) == kept
