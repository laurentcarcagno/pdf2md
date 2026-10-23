"""HTML rendering of tables with cell colours and text formatting, shared by PDF and Word."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from html import escape


@dataclass(frozen=True)
class Run:
    """A piece of text with one formatting; ``\\n`` in ``text`` is a line break."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    superscript: bool = False
    subscript: bool = False
    color: str | None = None  # "#rrggbb", None for the default text colour
    background: str | None = None  # highlight behind the text, "#rrggbb"

    def same_format(self, other: Run) -> bool:
        """Return True if ``other`` has the same formatting (texts are ignored)."""
        return self.format_key() == other.format_key()

    def with_text(self, text: str) -> Run:
        """Return a run with the same format and another text."""
        return replace(self, text=text)

    def format_key(self) -> tuple[object, ...]:
        """Everything but the text."""
        return (
            self.bold,
            self.italic,
            self.underline,
            self.strike,
            self.superscript,
            self.subscript,
            self.color,
            self.background,
        )


@dataclass
class HtmlCell:
    """One ``<td>``: paragraphs of runs, the background colour and the spans."""

    paragraphs: list[list[Run]] = field(default_factory=list)
    fill: str | None = None  # "#rrggbb"
    colspan: int = 1
    rowspan: int = 1


def merge_runs(runs: list[Run]) -> list[Run]:
    """Merge neighbouring runs with the same format.

    A blank run between two runs of the same format takes that format (an underline or a
    strikethrough runs through the space); between different formats it keeps its own.
    """
    runs = [run for run in runs if run.text]
    merged: list[Run] = []
    for index, run in enumerate(runs):
        following = next((r for r in runs[index + 1 :] if r.text.strip()), None)
        if (
            merged
            and not run.text.strip()
            and following is not None
            and merged[-1].same_format(following)
        ):
            run = merged[-1].with_text(run.text)
        if merged and merged[-1].same_format(run):
            merged[-1] = merged[-1].with_text(merged[-1].text + run.text)
        else:
            merged.append(run)
    return merged


_TAGS = (
    ("subscript", "sub"),
    ("superscript", "sup"),
    ("strike", "s"),
    ("underline", "u"),
    ("italic", "i"),
    ("bold", "b"),
)


def render_run(run: Run) -> str:
    """Return the HTML of one run: nested ``b i u s sup sub`` tags and a colour span."""
    html = escape(run.text, quote=False).replace("\n", "<br>")
    for attribute, tag in _TAGS:
        if getattr(run, attribute):
            html = f"<{tag}>{html}</{tag}>"
    style = []
    if run.color:
        style.append(f"color:{run.color}")
    if run.background:
        style.append(f"background-color:{run.background}")
    if style:
        html = f'<span style="{";".join(style)}">{html}</span>'
    return html


def _render_paragraph(paragraph: list[Run]) -> str:
    """Render the runs of one paragraph, without leading or trailing blanks."""
    runs = merge_runs(paragraph)
    if runs:
        runs[0] = replace(runs[0], text=runs[0].text.lstrip())
        runs[-1] = replace(runs[-1], text=runs[-1].text.rstrip())
    return "".join(render_run(run) for run in runs)


def render_cell(cell: HtmlCell) -> str:
    """Return the ``<td>`` of a cell."""
    attributes = ""
    if cell.colspan > 1:
        attributes += f' colspan="{cell.colspan}"'
    if cell.rowspan > 1:
        attributes += f' rowspan="{cell.rowspan}"'
    if cell.fill:
        attributes += f' style="background-color:{cell.fill}"'
    paragraphs = [_render_paragraph(paragraph) for paragraph in cell.paragraphs]
    return f"<td{attributes}>{'<br>'.join(p for p in paragraphs if p)}</td>"


def render_table_html(rows: list[list[HtmlCell]]) -> str:
    """Return the table as HTML, one ``<tr>`` per line and no blank line (Markdown-safe)."""
    lines = ['<table border="1" cellspacing="0" cellpadding="4">']
    lines.extend("<tr>" + "".join(render_cell(cell) for cell in row) + "</tr>" for row in rows)
    lines.append("</table>")
    return "\n".join(lines)


def hex_color(red: float, green: float, blue: float) -> str:
    """Return ``#rrggbb`` from 0..1 channels."""
    return "#" + "".join(f"{round(min(max(c, 0.0), 1.0) * 255):02x}" for c in (red, green, blue))
