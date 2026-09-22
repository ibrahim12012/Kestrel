# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
ai/file_merging.py
=====================
Merges several EXISTING files - any mix of images and documents, order
preserved - into one combined PDF. Backs ai.actions.tool_merge_files_to_pdf.

This is the read/combine counterpart to ai/file_generation.py (which
only ever writes a BRAND NEW file from text the AI itself authored - it
has no path in, nothing to merge) and reuses ai/file_context.py's
extraction for the text-based inputs, so a .docx or .txt merged in here
is read exactly the way it would be anywhere else in the app.

Design: rather than hand-rolling page layout for every possible source
type inside one function, every input first becomes its own small,
single-purpose PDF (one page for an image, however many pages
reportlab needs for extracted document text - reusing
ai.file_generation.generate_pdf, the exact same renderer
tool_generate_file itself uses, so a merged document page looks
consistent with one Kestrel generates from scratch), and pypdf then
stitches all of those together. One conversion story per type, one
merge step, instead of N page-layout implementations.

Supported per file, auto-detected by extension - nothing needs to be
told what a given path actually is:
  - Images (.png, .jpg, .jpeg, .bmp, .gif, .tiff, .tif, .webp) - each
    becomes one full page, via Pillow.
  - .pdf - every page copied in as-is, via pypdf.
  - .docx and the same plain-text formats ai.file_context already reads
    for chat attachments (.txt, .md, .csv, .log, .json, and more - see
    ai.file_context.PLAIN_TEXT_EXTENSIONS) - text extracted the same
    way a dropped-in chat attachment is, then rendered into new PDF
    page(s) titled with that file's own name.

A file that's missing, unreadable, or an unsupported type is SKIPPED,
not a hard failure - see merge_to_pdf's own docstring for exactly how
a partial-success run is reported back.

Known simplification (not a bug, just a v1 boundary): an image becomes
a PDF page sized to its own pixel dimensions, not resized to fit a
standard page - a very large source image can produce a very large
page. Fine for the common case (scans, photos, screenshots); worth
knowing if someone's merging something unusual.
"""

from __future__ import annotations

import os
import tempfile

from ai import file_generation
from ai import file_context


class MergeError(Exception):
    """
    Raised only when NOTHING could be merged at all (bad input, or
    every single file was skipped) - there'd be nothing to save. A
    partial success (some files merged, some skipped) is NOT this - see
    merge_to_pdf's own return value for that case instead.
    """


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".tif", ".webp"}
PDF_EXTENSION = ".pdf"
DOCX_EXTENSION = ".docx"


def _image_to_temp_pdf(path: str, tmpdir: str) -> str:
    from PIL import Image
    img = Image.open(path)
    # Pillow's PDF writer needs a mode it actually knows how to encode
    # RGBA/P/LA (transparency, palette) aren't valid PDF image modes.
    if img.mode not in ("RGB", "L", "1", "CMYK"):
        img = img.convert("RGB")
    out_path = os.path.join(tmpdir, os.path.basename(path) + ".page.pdf")
    img.save(out_path, "PDF")
    return out_path


def _text_to_temp_pdf(text: str, title: str, tmpdir: str) -> str:
    # "# title" is ai.file_generation's own title-line convention (see
    # parse_sections) - reusing it here keeps a merged text page looking
    # like every other PDF Kestrel produces, title heading and all.
    content = f"# {title}\n\n{text}" if text.strip() else f"# {title}\n\n(empty)"
    out_path = os.path.join(tmpdir, title + ".page.pdf")
    file_generation.generate_pdf(out_path, content)
    return out_path


def _convert_one(path: str, tmpdir: str) -> str:
    """
    Returns the path to a temp single-purpose PDF standing in for
    `path`, or raises MergeError/any exception on failure - the caller
    (merge_to_pdf) is what turns that into a skip entry rather than a
    hard stop.
    """
    ext = os.path.splitext(path)[1].lower()
    name = os.path.splitext(os.path.basename(path))[0]

    if ext in IMAGE_EXTENSIONS:
        return _image_to_temp_pdf(path, tmpdir)

    if ext == PDF_EXTENSION:
        return path  # already a PDF - appended as-is, no conversion needed

    if ext == DOCX_EXTENSION or ext in file_context.PLAIN_TEXT_EXTENSIONS:
        text, err = file_context.extract_text_from_file(path)
        if err:
            raise MergeError(err)
        return _text_to_temp_pdf(text, name, tmpdir)

    raise MergeError(f"unsupported file type '{ext or '(none)'}'")


def merge_to_pdf(paths: "list[str]", output_filename: str) -> "tuple[str, list[str]]":
    """
    Merges `paths` (existing files, any supported mix) in order into one
    PDF saved under file_generation.generated_files_dir() - the same
    Documents\\Kestrel Files folder tool_generate_file uses.

    Returns (output_path, skipped) - skipped is a list of
    "filename: reason" strings for anything that couldn't be included
    (missing file, unsupported extension, unreadable/corrupt content).
    The merge still succeeds with everything else that could be read.

    Raises MergeError only if EVERY file was skipped (or none were
    given) - there'd be nothing to save at all.
    """
    from pypdf import PdfWriter

    if not paths:
        raise MergeError("no files were given to merge.")

    writer = PdfWriter()
    skipped: list[str] = []
    included = 0

    with tempfile.TemporaryDirectory(prefix="kestrel_merge_") as tmpdir:
        for path in paths:
            display_name = os.path.basename(path) if path else "(empty path)"
            if not path or not os.path.isfile(path):
                skipped.append(f"{display_name}: file not found")
                continue
            try:
                page_pdf = _convert_one(path, tmpdir)
                writer.append(page_pdf)
                included += 1
            except MergeError as e:
                skipped.append(f"{display_name}: {e}")
            except Exception as e:
                skipped.append(f"{display_name}: {e}")

        if included == 0:
            reason = ("; ".join(skipped)) if skipped else "no files given"
            raise MergeError(f"nothing could be merged - {reason}.")

        directory = file_generation.generated_files_dir()
        safe_name = file_generation._sanitize_filename(output_filename, "pdf")
        out_path = file_generation._unique_path(directory, safe_name)
        # Written while tmpdir is still alive - pypdf's PdfWriter can
        # keep referencing the on-disk temp pages right up until the
        # final write, so the whole write has to happen inside this
        # `with` block, not after it exits and cleans them up.
        with open(out_path, "wb") as f:
            writer.write(f)

    return out_path, skipped
