# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tests/test_verify_build.py
==========================
Covers tools/verify_build.py's PE header parsing.

That script is what answers "is this flagged build from before or after
the Kestrel.spec fixes", so it has to be right about offsets nobody
checks by eye. The fixtures here are minimal PE files built byte by
byte, which is the only way to assert against a layout that is known
exactly rather than against whatever binary happens to be lying around
(and there is no Windows binary in this repo to test against anyway).

The offset that matters most is the data directory: PE32 puts it at
optional-header+96 and PE32+ at +112, because the windows-specific
fields differ in width between the two. Reading the wrong one reports a
signed binary as unsigned or vice versa, silently.
"""

from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import verify_build as vb


PE32 = 0x10B
PE32_PLUS = 0x20B


def _make_pe(sections, cert_size: int = 0, magic: int = PE32_PLUS) -> bytes:
    """A structurally valid PE with the given section names, optionally
    carrying a certificate table entry."""
    size_opt = 240 if magic == PE32_PLUS else 224
    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x40)          # e_lfanew

    coff = bytearray(24)                              # "PE\0\0" + 20 byte COFF
    coff[0:4] = b"PE\0\0"
    struct.pack_into("<H", coff, 4 + 2, len(sections))
    struct.pack_into("<H", coff, 4 + 16, size_opt)

    opt = bytearray(size_opt)
    struct.pack_into("<H", opt, 0, magic)
    dirs = 112 if magic == PE32_PLUS else 96
    struct.pack_into("<I", opt, dirs + 32, 0x1000)    # cert table RVA
    struct.pack_into("<I", opt, dirs + 36, cert_size)

    table = bytearray()
    for name in sections:
        header = bytearray(40)
        header[0:8] = name.encode().ljust(8, b"\0")[:8]
        table += header

    return bytes(dos + coff + opt + table)


def _version_blob(**fields) -> bytes:
    blob = "VS_VERSION_INFO".encode("utf-16-le") + b"\0\0"
    for key, value in fields.items():
        blob += key.encode("utf-16-le") + b"\0\0\0\0" + value.encode("utf-16-le") + b"\0\0"
    return blob


# ── section table ────────────────────────────────────────────────────

def test_reads_section_names():
    pe = _make_pe([".text", ".rdata", ".data", ".rsrc"])
    assert vb.section_names(pe) == [".text", ".rdata", ".data", ".rsrc"]


def test_upx_sections_are_recognised_as_packing():
    assert vb.packer_of(_make_pe(["UPX0", "UPX1", ".rsrc"])) == "UPX"


def test_an_ordinary_binary_reports_no_packer():
    assert vb.packer_of(_make_pe([".text", ".rdata", ".rsrc"])) is None


def test_other_packers_are_named_rather_than_missed():
    assert vb.packer_of(_make_pe([".vmp0", ".text"])) == "VMProtect"
    assert vb.packer_of(_make_pe([".MPRESS1"])) == "MPRESS"


# ── certificate table ────────────────────────────────────────────────

def test_unsigned_binary_has_no_certificate_table():
    assert vb.is_signed(_make_pe([".text"], cert_size=0)) is False


def test_signed_binary_is_detected_pe32_plus():
    assert vb.is_signed(_make_pe([".text"], cert_size=9032)) is True


def test_signed_binary_is_detected_pe32():
    """PE32's data directory sits 16 bytes earlier than PE32+'s. Reading
    the PE32+ offset on a PE32 file lands in the middle of the last
    directory entry and reports whatever happens to be there."""
    assert vb.is_signed(_make_pe([".text"], cert_size=4096, magic=PE32)) is True
    assert vb.is_signed(_make_pe([".text"], cert_size=0, magic=PE32)) is False


def test_unknown_optional_header_magic_is_not_claimed_as_signed():
    assert vb.is_signed(_make_pe([".text"], cert_size=9032, magic=0xFFFF)) is False


# ── version resource ─────────────────────────────────────────────────

def test_version_resource_presence():
    assert vb.has_version_resource(_version_blob(CompanyName="Kestrel"))
    assert not vb.has_version_resource(b"no resource here")


def test_version_fields_are_read_back():
    blob = _version_blob(
        CompanyName="Kestrel",
        ProductName="Kestrel",
        FileDescription="AI desktop assistant and automation for Windows",
        FileVersion="0.2.1.0",
    )
    fields = vb.version_strings(blob)
    assert fields["CompanyName"] == "Kestrel"
    assert fields["FileVersion"] == "0.2.1.0"
    assert fields["FileDescription"].startswith("AI desktop assistant")


def test_missing_version_fields_are_simply_absent():
    fields = vb.version_strings(_version_blob(CompanyName="Kestrel"))
    assert "CompanyName" in fields
    assert "ProductName" not in fields


# ── manifest ─────────────────────────────────────────────────────────

def test_manifest_detection():
    assert vb.has_manifest(b'<requestedExecutionLevel level="asInvoker" uiAccess="false"/>')
    assert not vb.has_manifest(b"<assembly></assembly>")


# ── malformed input ──────────────────────────────────────────────────

def test_non_pe_input_raises_notpe_rather_than_an_index_error():
    """walk() over a dist folder hands this function .pyd files, resource
    blobs and the occasional truncated download. A stray IndexError
    there would abort the whole scan partway through."""
    for bad in (b"", b"MZ", b"MZ" + b"\0" * 100, b"not an executable"):
        try:
            vb.section_names(bad)
        except vb.NotPE:
            continue
        except Exception as e:                       # noqa: BLE001
            raise AssertionError(
                f"{type(e).__name__} leaked instead of NotPE for {bad[:16]!r}"
            ) from e
        raise AssertionError(f"no exception at all for {bad[:16]!r}")


def test_mz_without_pe_signature_is_rejected():
    """A DOS stub, or a renamed non-PE file, gets this far and no
    further."""
    data = bytearray(0x200)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 0x80)
    try:
        vb.section_names(bytes(data))
    except vb.NotPE:
        return
    raise AssertionError("accepted a file with no PE signature")
