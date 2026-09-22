# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/file_generation.py
========================
Turns AI-authored text into a real, openable file on disk - a plain
text/Markdown/CSV file, or an actual Word document / PDF / Excel
spreadsheet, not just a code block dumped into chat. Backs
ai.actions.tool_generate_file(); kept as its own module (rather than
inline in actions.py, which is already close to 1200 lines of mostly
desktop-automation code) since this is a genuinely separate concern -
document formatting - with its own optional dependencies.

Every generator here takes the SAME lightweight input shape (see
parse_sections below) so tool_generate_file can hand the model one
consistent authoring convention regardless of which format it picks:
a line starting with "# " is the title, "## " starts a heading, a
blank line separates paragraphs, and everything else is body text.
docx/pdf render that structurally (a real Heading 1/Heading 2 style,
not just bold text); txt/md pass it through close to verbatim.

The docx/pdf/xlsx generators depend on python-docx / reportlab /
openpyxl respectively - none of which were already dependencies of
this project (only pypdf, for *reading* an already-existing PDF
dropped into the chat - see ai/file_context.py). Each is imported
lazily, right inside its own generate_* function, and guarded with a
try/except that turns "not installed" into a clear, actionable
GenerationError instead of an ImportError traceback the model has no
way to act on - matching the same guarded-optional-import convention
ai/file_context.py and ai/discovery/accessibility.py already use for
their own optional dependencies.
"""

from __future__ import annotations

import csv
import io
import os
import re

import branding


class GenerationError(Exception):
    """Raised for any reason a file couldn't be produced - a missing
    optional dependency, unparsable content for the target format, or
    a filesystem error. The message is written to be shown to the model
    (and from there, to the user) as-is, so it's always a complete,
    actionable sentence rather than a bare exception repr."""


# ── Where generated files land ───────────────────────────────────────
# Deliberately the user's real Documents folder, NOT paths.USER_DATA_PATH
# (that's the app's own internal/hidden data directory - macros, the
# chat DB, the .env holding API keys - never meant to be something a
# user goes looking through). Something the AI generates FOR the user
# (a report, a letter, a spreadsheet) belongs somewhere they'd actually
# think to look for it, and would show up in a normal file-open dialog
# without hunting through AppData.
def default_generated_files_dir() -> str:
    """Where generated files land when nothing has been configured:
    Documents\\<app name> Files."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = docs if os.path.isdir(docs) else os.path.expanduser("~")
    return os.path.join(base, f"{branding.APP_NAME} Files")


def generated_files_dir() -> str:
    """
    The folder every generated file is written to, as configured in
    Settings (ai.main.get_generated_files_dir) - falling back to the
    default above when nothing is set, or when what IS set can't be
    created (a drive that isn't mounted any more, a path that's since
    become read-only). Falling back beats failing the whole
    file-generation call over a stale setting.
    """
    target = ""
    try:
        import ai.main as ai_main
        target = (ai_main.get_generated_files_dir() or "").strip()
    except Exception as e:
        print(f"[file_generation] Couldn't read the configured output folder: {e}")

    for candidate in (target, default_generated_files_dir()):
        if not candidate:
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
            return candidate
        except OSError as e:
            print(f"[file_generation] Can't use {candidate!r} ({e}) - falling back.")

    fallback = os.path.expanduser("~")
    return fallback


_EXTENSION_FOR_FORMAT = {
    "txt": ".txt", "md": ".md", "csv": ".csv",
    "docx": ".docx", "pdf": ".pdf", "xlsx": ".xlsx",
}

# Characters that are illegal in a Windows filename (this codebase is
# Windows-only - see requirements.txt's pywin32/uiautomation being the
# sys_platform=="win32"-gated ones, not optional cross-platform hedges)
# plus a couple that are merely awkward. Replaced rather than rejected
# outright - a model-supplied filename shouldn't fail the whole
# operation over one bad character it's easy to just swap out.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename(filename: str, fmt: str) -> str:
    name = filename.strip()
    ext = _EXTENSION_FOR_FORMAT[fmt]
    # Strip whatever extension is already there (right OR wrong one
    # a model asked for "pdf" but typed "report.txt" should still get
    # a .pdf file, not a .pdf-flavored file literally named report.txt).
    name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)
    name = _ILLEGAL_FILENAME_CHARS.sub("_", name).strip(" .") or "Untitled"
    return name + ext


def _unique_path(directory: str, filename: str) -> str:
    """
    Never silently overwrites an existing file - appends " (2)", " (3)",
    ... instead, same convention Windows Explorer itself uses for a
    duplicate download/copy. The user (or a previous run of this same
    tool) may still want the earlier version.
    """
    candidate = os.path.join(directory, filename)
    if not os.path.exists(candidate):
        return candidate
    stem, ext = os.path.splitext(filename)
    n = 2
    while True:
        candidate = os.path.join(directory, f"{stem} ({n}){ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


# ── Shared "# title / ## heading / body" parsing ─────────────────────

def parse_sections(content: str) -> "list[tuple[str, str]]":
    """
    Splits `content` into (kind, text) pairs, kind one of "title",
    "heading", "body" - the one authoring convention every docx/pdf/txt/
    md generator below shares. Blank lines separate body paragraphs;
    they don't need to be declared, just written naturally.
    """
    sections: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush():
        if paragraph:
            sections.append(("body", " ".join(paragraph).strip()))
            paragraph.clear()

    for raw_line in content.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush()
            continue
        if line.startswith("# "):
            flush()
            sections.append(("title", line[2:].strip()))
        elif line.startswith("## "):
            flush()
            sections.append(("heading", line[3:].strip()))
        else:
            paragraph.append(line.strip())
    flush()
    return sections


# ── Plain-text formats (no optional dependency) ──────────────────────

def generate_txt(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def generate_md(path: str, content: str) -> None:
    # Markdown IS the authoring convention already (# / ##), so this is
    # just a verbatim write - no reparsing needed.
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def generate_csv(path: str, content: str) -> None:
    """
    `content` must already be comma-separated rows (one row per line -
    build it that way rather than handing this prose). Re-parsed and
    re-written through csv.writer (rather than a raw text dump) so
    fields containing a comma or quote themselves come out correctly
    quoted, instead of silently corrupting the column count.
    """
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        raise GenerationError("No CSV rows found in the given content - it was empty.")
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(rows)


# ── Word documents (python-docx) ─────────────────────────────────────

def generate_docx(path: str, content: str) -> None:
    try:
        import docx
    except ImportError:
        raise GenerationError(
            "Can't create a Word document: the 'python-docx' package "
            "isn't installed. Install it with: pip install python-docx"
        )

    document = docx.Document()
    for kind, text in parse_sections(content):
        if kind == "title":
            document.add_heading(text, level=0)
        elif kind == "heading":
            document.add_heading(text, level=1)
        else:
            document.add_paragraph(text)
    document.save(path)


# ── PDF (reportlab) ───────────────────────────────────────────────────

def generate_pdf(path: str, content: str) -> None:
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    except ImportError:
        raise GenerationError(
            "Can't create a PDF: the 'reportlab' package isn't "
            "installed. Install it with: pip install reportlab"
        )

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        path, pagesize=LETTER,
        leftMargin=1 * inch, rightMargin=1 * inch,
        topMargin=1 * inch, bottomMargin=1 * inch,
    )
    flowables = []
    for kind, text in parse_sections(content):
        # XML-escape: reportlab's Paragraph interprets its text as a small
        # HTML-like markup language, so a literal "&"/"<"/">" typed by the
        # model would otherwise either vanish or break parsing outright.
        escaped = (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        if kind == "title":
            flowables.append(Paragraph(escaped, styles["Title"]))
            flowables.append(Spacer(1, 0.2 * inch))
        elif kind == "heading":
            flowables.append(Paragraph(escaped, styles["Heading2"]))
            flowables.append(Spacer(1, 0.12 * inch))
        else:
            flowables.append(Paragraph(escaped, styles["BodyText"]))
            flowables.append(Spacer(1, 0.12 * inch))
    if not flowables:
        flowables.append(Paragraph("", styles["BodyText"]))
    doc.build(flowables)


# ── Spreadsheets (openpyxl) ───────────────────────────────────────────

def generate_xlsx(path: str, content: str) -> None:
    """
    Same CSV-shaped `content` convention as generate_csv - parsed into
    rows/columns rather than treated as free text. The header row (the
    first one) is bolded, and every column is auto-widened to fit its
    longest cell, so the result looks like something a person built by
    hand rather than a raw data dump.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        raise GenerationError(
            "Can't create a spreadsheet: the 'openpyxl' package isn't "
            "installed. Install it with: pip install openpyxl"
        )

    rows = list(csv.reader(io.StringIO(content)))
    if not rows:
        raise GenerationError("No rows found in the given content - it was empty.")

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    widths: dict[int, int] = {}
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths.get(i, 0), len(str(value)))
    for i, width in widths.items():
        ws.column_dimensions[chr(65 + i)].width = min(width + 2, 60)

    wb.save(path)


_GENERATORS = {
    "txt": generate_txt, "md": generate_md, "csv": generate_csv,
    "docx": generate_docx, "pdf": generate_pdf, "xlsx": generate_xlsx,
}


def generate_file(filename: str, fmt: str, content: str) -> str:
    """
    The single entry point ai.actions.tool_generate_file calls. Picks
    the right generator for `fmt`, sanitizes/de-duplicates the filename,
    writes it under generated_files_dir(), and returns the full path -
    or raises GenerationError with a message that's already
    user/model-facing, never a raw traceback.
    """
    if fmt not in _GENERATORS:
        raise GenerationError(
            f"Unknown format '{fmt}'. Supported: {', '.join(sorted(_GENERATORS))}."
        )
    directory = generated_files_dir()
    safe_name = _sanitize_filename(filename, fmt)
    path = _unique_path(directory, safe_name)
    _GENERATORS[fmt](path, content)
    return path
