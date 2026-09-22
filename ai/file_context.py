# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/file_context.py
====================
Extracts plain text from a local file dropped into the chat input, so its
content can be included as context in the next message to Kestrel - the
drag-and-drop counterpart to the screenshot/image path (see
ai.main.trigger_screenshot): drop a .txt/.json/.pdf onto the chat instead
of attaching a picture.

Also backs ai.actions.tool_read_file - the AI's own on-demand "open and
read this file" tool, given a path rather than a drag-and-drop - and is
reused again by ai.file_merging for the text-based inputs to
tool_merge_files_to_pdf, so a .docx or .txt read there is extracted the
exact same way it would be anywhere else in the app. One extraction
function, three call sites, rather than three slightly-different ones
drifting apart.

Supported: plain-text-readable formats - general text (.txt, .json, .md,
.csv, .log, .tsv), config/data (.yaml, .yml, .toml, .ini, .cfg, .conf,
.xml, .env), markup/web (.html, .htm, .css), and common source files
(.py, .js, .ts, .java, .c, .cpp, .cs, .go, .rs, .rb, .php, .sql, .sh,
.bat, .ps1) - read directly; .pdf via pypdf, .docx via python-docx, and
.xlsx via openpyxl (all three optional dependencies - extract_text_from_file
returns a clear error if the relevant one isn't installed rather than
raising). Anything else is refused with a message naming what IS
supported - same "don't guess, say why" pattern as ai.safety's command
allow-list.

Image formats are a separate path, not a rejection: is_image_file() +
load_image_attachment() below hand a dropped photo/screenshot to
app/chat_widget.py as an actual image, which ai.main queues (see
ai.main.queue_chat_images) and the next ai()/ai_stream() call sends to
the model as a real ImagePart - genuine vision, the same content-part
type a manual screenshot or autonomous-mode's own vision loop already
uses (ai.providers.base.ImagePart). There's still no OCR step anywhere
in this module (nothing here turns pixels into a text string) - an
image was never routed through extract_text_from_file's "text" path
and still isn't; it's just no longer refused outright, because a
vision-capable model doesn't need OCR to read a photo of a whiteboard
or a screenshot of an error message, it needs to SEE it.
"""

from __future__ import annotations

import os

PLAIN_TEXT_EXTENSIONS = {
    ".txt", ".json", ".md", ".csv", ".log",
    # config / data
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".tsv",
    ".env",
    # markup / web
    ".html", ".htm", ".css",
    # code - dropping a source file in for Kestrel to read/reference is
    # the same "give it context" use case as a .txt or .csv
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".h", ".cpp",
    ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sql", ".sh", ".bat",
    ".ps1",
}
PDF_EXTENSION = ".pdf"
DOCX_EXTENSION = ".docx"
XLSX_EXTENSION = ".xlsx"
SUPPORTED_EXTENSIONS = PLAIN_TEXT_EXTENSIONS | {PDF_EXTENSION, DOCX_EXTENSION, XLSX_EXTENSION}

# Formats every provider's ImagePart already knows how to send (PIL can
# open all of these) - kept separate from SUPPORTED_EXTENSIONS since
# these never go through extract_text_from_file at all; see
# is_image_file()/load_image_attachment() below.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}

MAX_FILE_CONTEXT_CHARS = 20000  # per file - keeps one huge file from blowing out the context window
MAX_IMAGE_ATTACHMENT_BYTES = 20 * 1024 * 1024  # generous for a real screenshot/photo, but a much bigger drop is more likely the wrong file than a deliberate attachment


def extract_text_from_file(path: str) -> "tuple[str | None, str | None]":
    """
    Returns (text, None) on success, or (None, error_message) on failure.
    Never raises. Image files are NOT handled here - see is_image_file()
    / load_image_attachment() instead; callers (app/chat_widget.py's
    _handle_dropped_file) branch on is_image_file() before ever reaching
    this function.
    """
    if not os.path.isfile(path):
        return None, "File not found."

    ext = os.path.splitext(path)[1].lower()

    if ext == PDF_EXTENSION:
        text, err = _extract_pdf_text(path)
    elif ext == DOCX_EXTENSION:
        text, err = _extract_docx_text(path)
    elif ext == XLSX_EXTENSION:
        text, err = _extract_xlsx_text(path)
    elif ext in PLAIN_TEXT_EXTENSIONS:
        text, err = _extract_plain_text(path)
    else:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        import ai.main as ai_main
        return None, f"Unsupported file type '{ext or '(none)'}'. {ai_main.get_assistant_name()} can read: {supported}."

    if err:
        return None, err

    if len(text) > MAX_FILE_CONTEXT_CHARS:
        text = text[:MAX_FILE_CONTEXT_CHARS] + "\n... (truncated)"

    return text, None


def file_dialog_filter() -> str:
    """
    The supported extensions as a QFileDialog filter string, e.g.
    "*.txt *.md *.pdf *.png".

    Built from the same sets the accept path checks, so the dialog can
    never offer a file the extractor will then refuse, and never hide
    one it would have accepted. Two hand-maintained lists would drift
    the first time an extension was added to one of them.
    """
    everything = sorted(SUPPORTED_EXTENSIONS | IMAGE_EXTENSIONS)
    return " ".join(f"*{ext}" for ext in everything)


def is_image_file(path: str) -> bool:
    """True for any extension app/chat_widget.py should route to
    load_image_attachment() instead of extract_text_from_file()."""
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def load_image_attachment(path: str) -> "tuple[object | None, str | None]":
    """
    Loads a dropped image file as a PIL Image ready to hand straight to
    ai.providers.base.ImagePart - same (value, None) / (None, error)
    contract as extract_text_from_file, so app/chat_widget.py's calling
    code reads almost identically for both.
    """
    if not os.path.isfile(path):
        return None, "File not found."
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, f"Could not read file: {e}"
    if size > MAX_IMAGE_ATTACHMENT_BYTES:
        limit_mb = MAX_IMAGE_ATTACHMENT_BYTES // (1024 * 1024)
        return None, f"Image is {size / (1024 * 1024):.1f} MB - over the {limit_mb} MB attachment limit."
    try:
        from PIL import Image
        image = Image.open(path)
        # .load() forces the actual pixel read right now, inside this
        # try - Image.open() alone is lazy, so a corrupt/truncated file
        # would otherwise raise much later (inside ImagePart.to_png_bytes,
        # mid-API-call) instead of here where it can be reported cleanly.
        image.load()
        return image, None
    except Exception as e:
        return None, f"Could not read image: {e}"


def format_attachment_block(filename: str, text: str) -> str:
    """The exact text handed to Gemini alongside the user's typed message."""
    return f"[Attached file: {filename}]\n{text}"


def _extract_plain_text(path: str) -> "tuple[str | None, str | None]":
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(), None
    except Exception as e:
        return None, f"Could not read file: {e}"


def _extract_pdf_text(path: str) -> "tuple[str | None, str | None]":
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, "PDF support needs the 'pypdf' package (pip install pypdf)."
    try:
        reader = PdfReader(path)
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join(pages).strip()
        if not text:
            return None, "No extractable text found in this PDF (it may be a scanned image)."
        return text, None
    except Exception as e:
        return None, f"Could not read PDF: {e}"


def _extract_docx_text(path: str) -> "tuple[str | None, str | None]":
    try:
        import docx
    except ImportError:
        return None, "Word document support needs the 'python-docx' package (pip install python-docx)."
    try:
        document = docx.Document(path)
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
        # Tables carry real content too (this is a common shape for
        # invoices/forms) - without this, a .docx that's mostly a table
        # would extract as almost empty.
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    paragraphs.append(" | ".join(cells))
        text = "\n\n".join(paragraphs).strip()
        if not text:
            return None, "No extractable text found in this Word document (it may be empty or image-only)."
        return text, None
    except Exception as e:
        return None, f"Could not read Word document: {e}"


def _extract_xlsx_text(path: str) -> "tuple[str | None, str | None]":
    try:
        from openpyxl import load_workbook
    except ImportError:
        return None, "Spreadsheet support needs the 'openpyxl' package (pip install openpyxl)."
    try:
        wb = load_workbook(path, data_only=True, read_only=True)
        sheets_text = []
        for ws in wb.worksheets:
            rows_text = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(v) for v in row if v is not None]
                if cells:
                    rows_text.append(", ".join(cells))
            if rows_text:
                sheets_text.append(f"[Sheet: {ws.title}]\n" + "\n".join(rows_text))
        text = "\n\n".join(sheets_text).strip()
        if not text:
            return None, "No data found in this spreadsheet (it may be empty)."
        return text, None
    except Exception as e:
        return None, f"Could not read spreadsheet: {e}"
