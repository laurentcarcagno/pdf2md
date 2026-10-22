# pdf2md

Multi-pass PDF to Markdown converter (with a Word `.docx` counterpart, see below). For each PDF it writes a clean `.md` file and a `.json`
mapping that links every Markdown block to its page and coordinates in the source PDF.

## Workflow

Pass 1 classifies every page (PyMuPDF): `native_text`, `multi_column`, `scanned`, `garbled`
(text layer made of unmapped glyphs) or `empty` (skipped). Each page then follows a fallback
chain; the first engine that returns content for a page wins, and a failing engine never stops
the document.

| Page kind | Chain |
|---|---|
| native text | geometric > docling > marker > pymupdf |
| multi-column (or most pages complex) | docling > marker > geometric > pymupdf |
| scanned / garbled | ocr > docling > marker > pymupdf |

- **geometric** (`extract.py`, `clean.py`, `structure.py`; pdfplumber): exact line coordinates,
  recurring header/footer detection: lines in the top/bottom quarter of the page whose text
  recurs on at least 40 % of the pages at the same height (±3 pt). In the outer 12 % band digits
  are normalised (`3 / 72` and `4 / 72` match); in the rest of the quarter the text must be
  identical, so tall headers are caught without taking a repeated heading for a header. Once a
  text is known, it is tagged wherever it sits in the band (first pages often shift it),
  headings (font size or numbering), paragraphs, lists, monospace blocks, tables.
- **ocr** (`ocr.py`): 300 dpi render, OpenCV deskew and denoise, then Tesseract (CLI, TSV with
  word boxes) and RapidOCR as second choice. The recognised lines go through the same cleaning
  and structuring as native pages.
- **docling** (`docling_engine.py`): layout and table models, native header/footer/index labels.
- **marker** (`marker_engine.py`): deep-learning Markdown; no coordinates (`null` in the JSON).
- **pymupdf** (`fallback.py`): plain text blocks with boxes, last resort.

After the engines: `toc.py` tags tables of contents / figures / tables (titled sections running
across pages, FR and EN), `text.py` repairs text with ftfy, `filters.py` applies `--keep`.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[all,dev]'     # core + OCR + docling + dev tools
.venv/bin/pip install -e '.[ocr,dev]'     # lighter: OCR only (no torch)
sudo apt install tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng   # OCR engine of choice
```

GPU: PyPI's default `torch` is the NVIDIA build. On an AMD GPU (tested: Radeon RX 9070 XT,
ROCm 7.1 on the host) install the ROCm build instead, after removing the `nvidia-*` packages:

```bash
.venv/bin/pip install torch==2.14.1 torchvision --index-url https://download.pytorch.org/whl/rocm7.2 \
    --extra-index-url https://pypi.org/simple
.venv/bin/python -c "import torch; print(torch.cuda.is_available())"   # True on ROCm too
```

Docling and Marker then use the GPU automatically; OCR (Tesseract, RapidOCR) stays on the CPU.

Marker is optional. Its `marker-pdf` requirement `pillow<11` has no wheel on Python 3.14, so
there it is installed without dependency resolution: `pip install --no-deps marker-pdf surya-ocr`
then `pip install anthropic google-genai markdown2 markdownify "openai<3" pdftext rapidfuzz
scikit-learn platformdirs`. Marker's code is Apache-2.0; its model weights use a modified
OpenRAIL-M licence (free for research, personal use and small companies).

## Dependencies and why

| Dependency | Why | Cost |
|---|---|---|
| pymupdf, pdfplumber | classification, rendering, fallback; exact line boxes | light |
| pydantic | validated settings and data models | light |
| python-docx | reads Word files (styles, numbering, tables, images) | light |
| tqdm | progress bar (already pulled in by docling) | light |
| ftfy | repairs mojibake and ligatures in extracted text | light |
| opencv-python-headless | deskew/denoise before OCR | ~50 MB |
| rapidocr, onnxruntime | second OCR engine, better on noisy scans | ~200 MB |
| docling | layout/table models for complex pages | torch, models (GBs) |
| marker-pdf | second deep-learning fallback | torch, models (GBs) |

Docling and Marker only run on pages the geometric pass cannot handle.

## Usage

```bash
.venv/bin/python -m pdf2md doc.pdf -o out/ [options]
./convert_dir.sh [options] INPUT_DIR      # every PDF of a directory -> INPUT_DIR/md/ (see --help)
```

| Option | Meaning |
|---|---|
| `--keep ELEMENT...` | keep `header footer toc lof lot` (or `all`); everything not listed is removed. Default: remove all |
| `-p`, `--pages RANGES` | convert only these pages: `10-` (10 to end), `-10` (1 to 10), `15-18`, `7`, or several `1-5,10-20,30-` (brackets accepted). Page numbers in the output stay those of the PDF; ranges past the last page are ignored |
| `--engine` | `auto` (default), `geometric`, `docling`, `marker`, `ocr`: force one engine (then pymupdf) |
| `--ocr-lang`, `--ocr-dpi`, `--no-ocr` | OCR languages (default `fra+eng`), resolution (300), disable |
| `--anchors` | prefix each block with `<!-- p:ID page:N -->` |
| `--no-images` | do not extract figures (by default they are saved to `<output>/<pdf name>/assets/` and referenced from the Markdown) |
| `--tables-as-images [MIN_ROWS]` | render each table (of at least MIN_ROWS rows, default 1) as a PNG of its area in the page (`assets/pNNNN_tNN.png`), alt text = the "Table …" caption when there is one; the JSON keeps the coordinates. Tables without coordinates or tagged as TOC/LOF/LOT stay text; needs images (incompatible with `--no-images`) |
| `--tables-keep-text` | with `--tables-as-images`, keep the table text as Markdown in a `<details>` block under the image |
| `--image-dpi N` | resolution of the saved PNG figures (default 200) |
| `--no-json` | write only the Markdown, no `.json` mapping |
| `--no-progress` | hide the progress bar (it is also hidden automatically when stderr is not a terminal) |
| `-v` | debug logs |

A page progress bar (tqdm, on stderr) shows `[i/n] file`, pages done, speed and the engine
running. Logs report the chain followed by each group of pages, pages resolved and time per
engine, page kinds, blocks per source, removed elements and total time.

Exit code is 1 if a file could not be opened. Thresholds are in `pdf2md.models.Settings`.

## Images

Figures are rendered from the page region to `<output>/<pdf name>/assets/pNNNN_KK.png` and
inserted in the Markdown at their vertical position as `![caption](assets/pNNNN_KK.png)`; the
alt text is the neighbouring `Figure N: ...` paragraph when there is one. Detected: raster
pictures and vector figures (a drawing cluster with curves or diagonal segments; tables, which are
made of horizontal/vertical lines, are not mistaken for figures). Skipped: images repeated on many
pages **in the page margins** (logos; identical pictures are recognised by content hash, not xref),
tiny or extreme-aspect images, the full-page picture of a scanned page, and a
drawing that merely outlines a table already in the text. Pictures side by side on one line (e.g. requirement ids built from pieces such as `HAR SSOW-` +
`10 /` + `Routing drawings`) are merged into one image, rendered at 300 dpi; as such a strip is
text drawn as a picture, Tesseract reads it and the text becomes its alt text, so it is
searchable and appears in the JSON `snippet`. A repeated strip in the body (the same `HAR SSOW-`
piece on every page) is kept; only repeats in the top/bottom margin count as logos. Mapping entries have `kind: "image"`
and an `image_path`. Blocks from Marker have no coordinates, so the images of such pages are
appended at the end of the page.

## Output

Each PDF gets its own folder, `<output>/<pdf name>/`:

```
<output>/<pdf name>/
    <pdf name>.md       Markdown (images referenced as assets/...)
    <pdf name>.json     traceability mapping (unless --no-json)
    assets/             figures; created when the first image is found
```

`<name>.md`: `#` headings, paragraphs, lists, fenced monospace blocks, Markdown tables.

`<name>.json`: one entry per Markdown block, in order:

```json
{"paragraph_id": 41, "page_number": 13, "paragraph_number_in_page": 2, "kind": "table",
 "snippet": "N° | Titre | ...", "source_pass": "pass2_pdfplumber", "element": null,
 "coordinates": {"x0": 77.0, "x1": 518.5, "top": 93.4, "bottom": 265.3}}
```

`kind` is `heading|paragraph|list|code|table|toc|image`; `element` is `header|footer|toc|
list_of_figures|list_of_tables` for tagged blocks (only present with `--keep`), else `null`.
Coordinates are PDF points, origin top-left. They are `null` for Marker blocks, and approximate
for OCR pages (derived from the 300 dpi render after deskew). A word split by an end-of-line
hyphen keeps its hyphen (`com-pound`) so compounds like `pas-à-pas` stay intact.

## Post-processing plugins

A plugin is a Python script that transforms the produced Markdown (filtering, removing text,
renaming files, ...). `pdf2md`, `docx2md` and `convert_dir.sh` share these options:

| Option | Meaning |
|---|---|
| `--register-plugin "NAME" SCRIPT.py` | associate NAME with SCRIPT.py and **copy the script into the plugin folder**; registering an existing name replaces its script. Without input files, nothing is converted. Can be repeated |
| `--post-process NAME` | apply the plugin to each produced `.md`; repeat the option to chain plugins, applied **in the order given**, each receiving the output of the previous one |
| `--list-plugins` | show the registered plugins |
| `--plugin-dir DIR` | plugin folder (default: `$PDF2MD_PLUGIN_DIR`, else `~/.config/pdf2md/plugins`) |

```bash
pdf2md --register-plugin "remove notes" examples/plugins/remove_notes.py
pdf2md doc.pdf -o out --post-process "remove notes" --post-process "prefix"
./convert_dir.sh --post-process "remove notes" ~/docs/norms      # same, for a whole folder
```

The plugin folder holds the copied scripts and `registry.json` (`name -> script`).

**Writing a plugin.** The script defines `process(markdown, context)` and returns the new
Markdown, or a `PluginResult` to also rename the output files:

```python
from pdf2md.plugins import PluginContext, PluginResult


def process(markdown: str, context: PluginContext) -> str | PluginResult:
    return markdown.replace("Airbus Amber", "")  # just return the new text
    # return PluginResult(markdown=text, stem="new_name")  # rename <stem>.md and <stem>.json
```

`context` gives `plugin_name`, `source` (the PDF/Word file), `md_path`, `json_path` (None with
`--no-json`) and `assets_dir`. Examples: `examples/plugins/remove_notes.py`,
`examples/plugins/rename_with_prefix.py` and `examples/plugins/ads.py` (replaces the thin
requirement-strip images by their alt text in bold behind an anchor and deletes the image files).

Behaviour worth knowing:
- Registration only parses the script (no code runs) and checks that it defines `process`;
  the script is imported when it is used. **Plugins run with your privileges: register only
  scripts you trust.**
- Names are checked before any conversion starts: an unknown name stops the run (exit code 2).
- The file is rewritten after each plugin. If one raises, returns something invalid or asks for
  an invalid or already used file name, that file is reported as failed (exit code 1) and keeps
  the result of the plugins that succeeded; the other files go on.
- Renaming applies to the `.md` and `.json` files only (the folder and `assets/` keep their
  names, so image links stay valid), and never overwrites an existing file.
- The JSON mapping is not edited by plugins: its snippets describe the text as converted.

## Word files (`docx2md`)

```bash
.venv/bin/python -m pdf2md.docx_cli report.docx -o out/ [options]     # or: docx2md ...
./convert_dir.sh DIR        # converts the *.pdf (pdf2md) and *.docx (docx2md) of a directory
```

Same outputs as for PDFs (`<output>/<name>/<name>.md`, `.json`, `assets/`), same `--keep`,
`--pages`, `--no-images`, `--no-json`, `--anchors`, `--no-progress` and `-v`; no OCR or engine
options. The Word file is read directly (python-docx + its XML), without going through PDF:

- **Headings** from the style (`Heading N`, `Titre N`, `Title`) or the outline level, with the
  automatic number Word displays (`1.2.3`) put back in front of the text.
- **Lists** with Word's own numbering (`1.`, `a.`, `(i)`, bullets). As for PDFs, lettered
  top-level items (`a.`, `b.`) and `NOTE` paragraphs stay separate paragraphs; numbered and
  bulleted items form lists, nested by level.
- **Tables** (merged cells handled; one-column tables become paragraphs), **monospace
  paragraphs** as code blocks, text boxes as paragraphs.
- **Headers/footers** are read from the section parts (once each) and removed unless kept.
- **Table of contents, of figures, of tables** are recognised from `TOC` fields, content controls
  and the `TOC n` / `Table of Figures` styles, then removed unless kept (`--keep toc lof lot`).
- **Images** (inline or floating, PNG/JPEG/GIF/EMF/WMF as stored) go to `assets/`; an image
  repeated three times or more is treated as decoration. EMF/WMF are saved unchanged but most
  browsers cannot display them.
- **Page numbers are estimates**: Word stores no pagination. They come from the
  `lastRenderedPageBreak` marks of the last save, or from explicit page/section breaks when there
  are none. Documents never repaginated by Word can come out as one or two pages, which also
  limits what `--pages` can select. There are no coordinates (`null`).
- Not converted: footnotes/endnotes, comments, tracked-change deletions (insertions are kept),
  equations, legacy `.doc` (save as `.docx`).

## Development

```bash
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy src tests && .venv/bin/pytest
```

The PDF samples (`input/`, `test_inputs/`) and the conversion results (`out/`, `output/`) are
not versioned; the tests that need `test_inputs/*.pdf` are skipped when the folder is absent.
