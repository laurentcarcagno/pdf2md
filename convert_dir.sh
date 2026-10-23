#!/usr/bin/env bash
# Convert every PDF (pdf2md) and Word file (docx2md) of a directory; results go to
# <input_dir>/md by default.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_bin="${PDF2MD_PYTHON:-$script_dir/.venv/bin/python}"

usage() {
    cat <<'USAGE'
Usage: convert_dir.sh [OPTIONS] INPUT_DIR [-- PDF2MD_OPTIONS...]
       convert_dir.sh --register-plugin NAME SCRIPT.py | --list-plugins

Convert every *.pdf (pdf2md) and *.docx (docx2md) of INPUT_DIR (not recursive) to Markdown
+ JSON mapping. Options marked [PDF] are ignored for Word files.

Options:
  -o, --output-dir DIR     output directory (default: INPUT_DIR/md); each PDF gets its own
                           folder DIR/<pdf name>/ with <pdf name>.md, .json and assets/
  -p, --pages RANGES       pages to convert in each file (Word: estimated pages), ranges:
                             10-      page 10 to the end
                             -10      page 1 to 10
                             15-18    pages 15 to 18
                             1-5,10-20,30-   several ranges (brackets [..] are accepted)
                           a range beyond a PDF's last page is ignored
  -k, --keep ELEMENTS      elements to keep, space or comma separated; all others are removed.
                           Values: header footer toc lof lot all (default: none kept)
  -e, --engine ENGINE      [PDF] auto (default) | geometric | docling | marker | ocr
      --ocr-lang LANGS     [PDF] Tesseract languages (default: fra+eng)
      --ocr-dpi N          [PDF] OCR rendering resolution (default: 300)
      --no-ocr             [PDF] never run OCR
      --anchors            add <!-- p:ID page:N --> markers to the Markdown
      --no-images          do not extract figures (default: saved to <output>/<pdf>/assets/)
      --image-dpi N        [PDF] figure resolution (default: 200)
      --tables-as-images [N]  [PDF] render tables (of at least N rows, default 1) as images
      --tables-html        insert tables as HTML (cell colours, bold/italic/underline/strike text)
      --tables-keep-text   [PDF] with --tables-as-images, keep the table text under the image
      --no-json            do not write the JSON mapping, only the Markdown
      --no-progress        hide the progress bar
      --post-process NAME  apply the plugin NAME to each produced Markdown file; repeat the
                           option to chain plugins (applied in the order given)
      --register-plugin NAME SCRIPT.py
                           copy SCRIPT.py into the plugin folder under NAME (can be repeated;
                           INPUT_DIR is then optional: nothing is converted without it)
      --list-plugins       list the registered plugins
      --plugin-dir DIR     plugin folder (default: $PDF2MD_PLUGIN_DIR or ~/.config/pdf2md/plugins)
  -v, --verbose            debug logs
  -h, --help               show this help

Anything after "--" is passed unchanged to "python -m pdf2md" (PDF files only).

Environment:
  PDF2MD_PYTHON            Python interpreter to use (default: <script dir>/.venv/bin/python)

Examples:
  convert_dir.sh ~/docs/norms
  convert_dir.sh -p "1-5,30-" -k toc footer ~/docs/norms
  convert_dir.sh -e docling --ocr-lang eng -o /tmp/out ~/docs/norms

Exit code: 0 if every file converted, 1 if at least one failed, 2 on usage error.
USAGE
}

die() {
    echo "Error: $*" >&2
    echo "Try '$0 --help'." >&2
    exit 2
}

output_dir=""
input_dir=""
pages=""
common_args=()   # options understood by pdf2md and docx2md
pdf_args=()      # PDF-only options
keep=()
post_names=()
plugin_commands=()   # --register-plugin / --list-plugins, run once, not per file

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        -o|--output-dir) [[ $# -ge 2 ]] || die "$1 needs a value"; output_dir="$2"; shift 2 ;;
        -p|--pages) [[ $# -ge 2 ]] || die "$1 needs a value"; pages="$2"; common_args+=(--pages "$2"); shift 2 ;;
        -e|--engine) [[ $# -ge 2 ]] || die "$1 needs a value"; pdf_args+=(--engine "$2"); shift 2 ;;
        --ocr-lang) [[ $# -ge 2 ]] || die "$1 needs a value"; pdf_args+=(--ocr-lang "$2"); shift 2 ;;
        --image-dpi) [[ $# -ge 2 ]] || die "$1 needs a value"; pdf_args+=(--image-dpi "$2"); shift 2 ;;
        --ocr-dpi) [[ $# -ge 2 ]] || die "$1 needs a value"; pdf_args+=(--ocr-dpi "$2"); shift 2 ;;
        --no-ocr|--tables-keep-text) pdf_args+=("$1"); shift ;;
        --tables-as-images)
            if [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]]; then pdf_args+=("$1" "$2"); shift 2
            else pdf_args+=("$1"); shift; fi ;;
        --anchors|--no-json|--no-images|--no-progress|--tables-html) common_args+=("$1"); shift ;;
        --post-process) [[ $# -ge 2 ]] || die "$1 needs a value"; post_names+=("$2"); common_args+=(--post-process "$2"); shift 2 ;;
        --plugin-dir) [[ $# -ge 2 ]] || die "$1 needs a value"; common_args+=(--plugin-dir "$2"); shift 2 ;;
        --register-plugin)
            [[ $# -ge 3 ]] || die "$1 needs a plugin name and a script path"
            plugin_commands+=(--register-plugin "$2" "$3"); shift 3 ;;
        --list-plugins) plugin_commands+=(--list-plugins); shift ;;
        -v|--verbose) common_args+=(-v); shift ;;
        -k|--keep)
            shift
            [[ $# -gt 0 && "$1" != -* ]] || die "--keep needs at least one element"
            [[ "$1" =~ ^(header|footer|toc|lof|lot|all)(,(header|footer|toc|lof|lot|all))*$ ]] \
                || die "invalid --keep value '$1' (use header footer toc lof lot all)"
            while [[ $# -gt 0 && "$1" =~ ^(header|footer|toc|lof|lot|all)(,(header|footer|toc|lof|lot|all))*$ ]]; do
                IFS=',' read -r -a parts <<< "$1"
                keep+=("${parts[@]}")
                shift
            done
            ;;
        --) shift; pdf_args+=("$@"); break ;;
        -*) die "unknown option '$1'" ;;
        *)
            [[ -z "$input_dir" ]] || die "several input directories given ('$input_dir', '$1')"
            input_dir="${1%/}"
            shift
            ;;
    esac
done

if [[ ${#plugin_commands[@]} -gt 0 ]]; then
    [[ -x "$python_bin" ]] || die "$python_bin not found; create the venv (see README)"
    plugin_dir_args=()
    for ((i = 0; i < ${#common_args[@]}; i++)); do
        [[ "${common_args[i]}" == "--plugin-dir" ]] && plugin_dir_args=(--plugin-dir "${common_args[i + 1]}")
    done
    "$python_bin" -m pdf2md "${plugin_commands[@]}" "${plugin_dir_args[@]}" || exit $?
    [[ -n "$input_dir" ]] || exit 0
fi
[[ -n "$input_dir" ]] || { usage >&2; exit 2; }
[[ -d "$input_dir" ]] || die "'$input_dir' is not a directory"
[[ -x "$python_bin" ]] || die "$python_bin not found; create the venv (see README) or set PDF2MD_PYTHON"
if [[ -n "$pages" ]]; then
    # Fail once, before the loop, instead of once per PDF.
    "$python_bin" -I -c 'import sys; from pdf2md.pages import parse_page_spec; parse_page_spec(sys.argv[1])' \
        "$pages" 2>/dev/null || die "invalid --pages value '$pages' (examples: 10- , -10 , 15-18 , 1-5,10-20,30-)"
fi
if [[ ${#post_names[@]} -gt 0 ]]; then
    # Fail once, before the loop, instead of once per file.
    plugin_dir_arg=""
    for ((i = 0; i < ${#common_args[@]}; i++)); do
        [[ "${common_args[i]}" == "--plugin-dir" ]] && plugin_dir_arg="${common_args[i + 1]}"
    done
    "$python_bin" -I -c 'import sys
from pathlib import Path
from pdf2md.plugins import PluginError, load_plugins
try:
    load_plugins(sys.argv[2:], Path(sys.argv[1]) if sys.argv[1] else None)
except PluginError as exc:
    sys.exit(str(exc))' "$plugin_dir_arg" "${post_names[@]}" || die "invalid --post-process"
fi
if [[ ${#keep[@]} -gt 0 ]]; then
    common_args+=(--keep "${keep[@]}")
fi
output_dir="${output_dir:-$input_dir/md}"

shopt -s nullglob nocaseglob
pdfs=("$input_dir"/*.pdf)
docs=("$input_dir"/*.docx)
total=$((${#pdfs[@]} + ${#docs[@]}))
if [[ $total -eq 0 ]]; then
    echo "No PDF or Word file found in $input_dir" >&2
    exit 0
fi

failures=0
for pdf in "${pdfs[@]}"; do
    echo "==> $(basename "$pdf")"
    # One process per file so that a failure does not stop the others.
    if ! "$python_bin" -m pdf2md "$pdf" -o "$output_dir" "${common_args[@]}" "${pdf_args[@]}"; then
        failures=$((failures + 1))
    fi
done
for doc in "${docs[@]}"; do
    echo "==> $(basename "$doc")"
    if ! "$python_bin" -m pdf2md.docx_cli "$doc" -o "$output_dir" "${common_args[@]}"; then
        failures=$((failures + 1))
    fi
done

echo "Done: $((total - failures))/$total converted into $output_dir"
exit $((failures > 0 ? 1 : 0))
