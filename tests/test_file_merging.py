# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_file_merging.py
=============================
Tests for ai/file_merging.py - merging existing images/documents into
one combined PDF (backs ai.actions.tool_merge_files_to_pdf).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ai.file_merging as file_merging
import ai.file_generation as file_generation


@pytest.fixture(autouse=True)
def _redirect_generated_files_dir(tmp_path, monkeypatch):
    """Every merge in this file lands in a throwaway tmp dir, never the
    user's real Documents\\Kestrel Files folder."""
    out_dir = tmp_path / "Kestrel Files"
    out_dir.mkdir()
    monkeypatch.setattr(file_generation, "generated_files_dir", lambda: str(out_dir))
    return out_dir


def _make_image(path, color=(200, 50, 50), size=(40, 30)):
    from PIL import Image
    Image.new("RGB", size, color).save(path)


def _make_rgba_image(path):
    from PIL import Image
    Image.new("RGBA", (20, 20), (10, 20, 30, 128)).save(path)


def _make_pdf(path, text="Existing PDF content"):
    file_generation.generate_pdf(path, f"# Doc\n\n{text}")


def _pdf_page_count(path):
    from pypdf import PdfReader
    return len(PdfReader(path).pages)


def test_merge_single_image_produces_one_page_pdf(tmp_path):
    img = tmp_path / "photo.png"
    _make_image(str(img))

    out_path, skipped = file_merging.merge_to_pdf([str(img)], "merged")

    assert skipped == []
    assert out_path.endswith(".pdf")
    assert os.path.isfile(out_path)
    assert _pdf_page_count(out_path) == 1


def test_merge_rgba_image_is_converted_before_saving(tmp_path):
    # RGBA isn't a valid PDF image mode - this used to raise inside Pillow
    # before the RGB-conversion step existed.
    img = tmp_path / "transparent.png"
    _make_rgba_image(str(img))

    out_path, skipped = file_merging.merge_to_pdf([str(img)], "merged")

    assert skipped == []
    assert _pdf_page_count(out_path) == 1


def test_merge_existing_pdf_copies_its_pages(tmp_path):
    pdf = tmp_path / "existing.pdf"
    _make_pdf(str(pdf))

    out_path, skipped = file_merging.merge_to_pdf([str(pdf)], "merged")

    assert skipped == []
    assert _pdf_page_count(out_path) == _pdf_page_count(str(pdf))


def test_merge_text_file_renders_a_page(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("Some notes here.", encoding="utf-8")

    out_path, skipped = file_merging.merge_to_pdf([str(txt)], "merged")

    assert skipped == []
    assert _pdf_page_count(out_path) >= 1


def test_merge_docx_file_renders_a_page(tmp_path):
    import docx
    document = docx.Document()
    document.add_paragraph("Hello from docx.")
    docx_path = tmp_path / "letter.docx"
    document.save(str(docx_path))

    out_path, skipped = file_merging.merge_to_pdf([str(docx_path)], "merged")

    assert skipped == []
    assert _pdf_page_count(out_path) >= 1


def test_merge_mixed_types_in_order_all_included(tmp_path):
    img = tmp_path / "a_photo.png"
    _make_image(str(img))
    pdf = tmp_path / "b_doc.pdf"
    _make_pdf(str(pdf))
    txt = tmp_path / "c_notes.txt"
    txt.write_text("Mixed merge test.", encoding="utf-8")

    out_path, skipped = file_merging.merge_to_pdf(
        [str(img), str(pdf), str(txt)], "combined"
    )

    assert skipped == []
    # 1 (image) + however many pages the generated pdf has + 1+ (text)
    assert _pdf_page_count(out_path) >= 1 + _pdf_page_count(str(pdf)) + 1


def test_merge_skips_missing_file_but_still_succeeds(tmp_path):
    img = tmp_path / "real.png"
    _make_image(str(img))

    out_path, skipped = file_merging.merge_to_pdf(
        [str(img), str(tmp_path / "ghost.png")], "merged"
    )

    assert os.path.isfile(out_path)
    assert len(skipped) == 1
    assert "ghost.png" in skipped[0]
    assert "not found" in skipped[0]


def test_merge_skips_unsupported_extension_but_still_succeeds(tmp_path):
    img = tmp_path / "real.png"
    _make_image(str(img))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video")

    out_path, skipped = file_merging.merge_to_pdf([str(img), str(video)], "merged")

    assert os.path.isfile(out_path)
    assert len(skipped) == 1
    assert "clip.mp4" in skipped[0]
    assert "unsupported" in skipped[0]


def test_merge_raises_when_nothing_could_be_merged(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"nope")

    with pytest.raises(file_merging.MergeError):
        file_merging.merge_to_pdf([str(video)], "merged")


def test_merge_raises_on_empty_paths_list():
    with pytest.raises(file_merging.MergeError):
        file_merging.merge_to_pdf([], "merged")


def test_merge_output_name_is_sanitized_and_unique(tmp_path, _redirect_generated_files_dir):
    img = tmp_path / "a.png"
    _make_image(str(img))

    out1, _ = file_merging.merge_to_pdf([str(img)], "report")
    out2, _ = file_merging.merge_to_pdf([str(img)], "report")

    assert out1 != out2  # second call doesn't overwrite the first
    assert os.path.isfile(out1) and os.path.isfile(out2)


def test_merge_output_saved_under_generated_files_dir(tmp_path, _redirect_generated_files_dir):
    img = tmp_path / "a.png"
    _make_image(str(img))

    out_path, _ = file_merging.merge_to_pdf([str(img)], "report")

    assert os.path.dirname(out_path) == str(_redirect_generated_files_dir)
