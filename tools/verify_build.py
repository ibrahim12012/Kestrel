# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
tools/verify_build.py
=====================
Inspect a built dist/Kestrel/ and report the things that decide whether
antivirus flags it. Run it before zipping or shipping anything.

    python tools/verify_build.py
    python tools/verify_build.py path/to/dist/Kestrel

It answers one question that is otherwise guesswork: did this build
actually pick up the settings in Kestrel.spec, or is it an older one?
A flagged binary that still reports UPX packing and no version resource
was built before those were fixed, and rebuilding is the whole fix. A
flagged binary that passes every check here is the expected case for an
unsigned automation tool, and needs a certificate plus a false-positive
submission rather than another code change (see docs/ANTIVIRUS.md).

Reads the PE headers directly, no third-party modules, so it runs on a
bare Python on any machine. Reports, for the main executable:

  UPX packing        section names - a packed binary has UPX0/UPX1
  version resource   publisher/product/description in file properties
  manifest           the asInvoker declaration
  signature          whether a certificate table is present at all
  SHA-256            to publish next to the download

and scans every other binary in the folder for packing too, since a
packed DLL from a dependency counts against the whole bundle.
"""

from __future__ import annotations

import hashlib
import os
import sys

# Set by a packer, never by a normal linker. UPX is the one that matters
# here because it is what Kestrel.spec used to turn on; the others are
# listed because if one ever shows up it means something in the
# dependency chain arrived pre-packed.
PACKER_SECTIONS = {
    "UPX0": "UPX", "UPX1": "UPX", "UPX2": "UPX", ".UPX0": "UPX", ".UPX1": "UPX",
    ".aspack": "ASPack", ".adata": "ASPack",
    ".themida": "Themida", ".vmp0": "VMProtect", ".vmp1": "VMProtect",
    "petite": "Petite", ".MPRESS1": "MPRESS", ".MPRESS2": "MPRESS",
}

BINARY_SUFFIXES = (".exe", ".dll", ".pyd", ".so")


class NotPE(Exception):
    pass


def _read(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _pe_offsets(data: bytes) -> tuple[int, int, int]:
    """Return (coff_header_offset, optional_header_offset, section_table_offset)."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise NotPE("not a PE file (no MZ header)")
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    if len(data) < e_lfanew + 24 or data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise NotPE("not a PE file (no PE signature)")
    coff = e_lfanew + 4
    opt = coff + 20
    size_opt = int.from_bytes(data[coff + 16:coff + 18], "little")
    return coff, opt, opt + size_opt


def section_names(data: bytes) -> list[str]:
    coff, _opt, sect = _pe_offsets(data)
    count = int.from_bytes(data[coff + 2:coff + 4], "little")
    names = []
    for i in range(count):
        raw = data[sect + i * 40:sect + i * 40 + 8]
        if len(raw) < 8:
            break
        names.append(raw.rstrip(b"\0").decode("latin-1", "replace"))
    return names


def packer_of(data: bytes) -> "str | None":
    for name in section_names(data):
        packer = PACKER_SECTIONS.get(name)
        if packer:
            return packer
    return None


def is_signed(data: bytes) -> bool:
    """
    True if the PE carries a certificate table. That is presence, not
    validity: verifying a signature properly needs the OS trust store,
    which is signtool's job (`signtool verify /pa /v <file>`). Presence
    is still the useful check here, because the common state is no
    signature at all.
    """
    _coff, opt, _sect = _pe_offsets(data)
    magic = int.from_bytes(data[opt:opt + 2], "little")
    if magic == 0x10B:        # PE32
        dirs = opt + 96
    elif magic == 0x20B:      # PE32+
        dirs = opt + 112
    else:
        return False
    # Data directory index 4 is the Certificate Table: 8 bytes in, the
    # second 4 being its size.
    size = int.from_bytes(data[dirs + 36:dirs + 40], "little")
    return size > 0


def has_version_resource(data: bytes) -> bool:
    # Every VERSIONINFO resource carries this key as a UTF-16LE string.
    return "VS_VERSION_INFO".encode("utf-16-le") in data


def version_strings(data: bytes) -> dict:
    """
    Pull the handful of version fields worth eyeballing back out of the
    resource. Deliberately crude: it finds each key as UTF-16LE and
    reads the null-terminated UTF-16LE string that follows, which is
    enough to confirm the fields are populated without implementing a
    resource-directory walker.
    """
    found = {}
    for key in ("CompanyName", "ProductName", "FileDescription", "FileVersion"):
        needle = key.encode("utf-16-le")
        at = data.find(needle)
        if at < 0:
            continue
        pos = at + len(needle)
        # Skip the padding zeros between the key and its value.
        while pos + 1 < len(data) and data[pos:pos + 2] == b"\0\0":
            pos += 2
        end = pos
        while end + 1 < len(data) and data[end:end + 2] != b"\0\0":
            end += 2
        value = data[pos:end].decode("utf-16-le", "replace").strip()
        if value:
            found[key] = value
    return found


def has_manifest(data: bytes) -> bool:
    return b"requestedExecutionLevel" in data


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _find_dist(argv: list) -> str:
    if len(argv) > 1:
        return argv[1]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "dist", "Kestrel")


def main(argv: list) -> int:
    dist = _find_dist(argv)
    if not os.path.isdir(dist):
        print(f"No build found at {dist}")
        print("Build one first:  pyinstaller Kestrel.spec")
        return 2

    exe = os.path.join(dist, "Kestrel.exe")
    if not os.path.isfile(exe):
        candidates = [f for f in os.listdir(dist) if f.lower().endswith(".exe")]
        if not candidates:
            print(f"No .exe in {dist}")
            return 2
        exe = os.path.join(dist, candidates[0])

    print(f"Inspecting {exe}\n")
    data = _read(exe)
    problems = []

    packer = packer_of(data)
    if packer:
        print(f"  [FAIL] packed with {packer}")
        print(f"         Executable packing is one of the highest-weighted")
        print(f"         static heuristics there is. Kestrel.spec sets")
        print(f"         upx=False on both EXE and COLLECT - this build")
        print(f"         predates that, or the spec was changed back.")
        problems.append("packed")
    else:
        print("  [ok]   not packed")

    if has_version_resource(data):
        fields = version_strings(data)
        missing = [k for k in ("CompanyName", "ProductName", "FileDescription")
                   if k not in fields]
        if missing:
            print(f"  [WARN] version resource present but missing {', '.join(missing)}")
            problems.append("version fields")
        else:
            print("  [ok]   version resource present")
        for key, value in fields.items():
            print(f"           {key}: {value}")
    else:
        print("  [FAIL] no version resource")
        print("         An unsigned binary with blank file properties is")
        print("         scored on that alone. Kestrel.spec generates one;")
        print("         this build predates that.")
        problems.append("no version resource")

    if has_manifest(data):
        print("  [ok]   manifest present (declares its execution level)")
    else:
        print("  [WARN] no manifest found")
        problems.append("no manifest")

    if is_signed(data):
        print("  [ok]   carries a signature")
        print("         Verify it properly with:  signtool verify /pa /v <file>")
    else:
        print("  [WARN] unsigned")
        print("         Expected until you have a certificate. This is what")
        print("         SmartScreen's 'Unknown publisher' prompt is about,")
        print("         and no spec setting substitutes for it.")
        problems.append("unsigned")

    # A packed DLL from a dependency counts against the bundle even when
    # the main executable is clean.
    packed_others = []
    for root, _dirs, files in os.walk(dist):
        for name in files:
            if not name.lower().endswith(BINARY_SUFFIXES):
                continue
            path = os.path.join(root, name)
            if os.path.abspath(path) == os.path.abspath(exe):
                continue
            try:
                found = packer_of(_read(path))
            except (NotPE, OSError):
                continue
            if found:
                packed_others.append((os.path.relpath(path, dist), found))

    print()
    if packed_others:
        print(f"  [FAIL] {len(packed_others)} other packed binaries in the bundle:")
        for rel, found in packed_others[:10]:
            print(f"           {rel} ({found})")
        if len(packed_others) > 10:
            print(f"           ... and {len(packed_others) - 10} more")
        problems.append("packed dependencies")
    else:
        print("  [ok]   no other packed binaries in the bundle")

    print(f"\n  SHA-256: {sha256(exe)}")
    print("  Publish this next to the download so anyone who gets a")
    print("  warning can verify they have the file you built.")

    print()
    if "packed" in problems or "no version resource" in problems:
        print("VERDICT: this build predates the Kestrel.spec fixes. Rebuild")
        print("         it before doing anything else - a detection on this")
        print("         binary says nothing about the current spec.")
        print("           pyinstaller --clean Kestrel.spec")
        return 1

    remaining = [p for p in problems if p != "unsigned"]
    if remaining:
        print(f"VERDICT: build is current but has issues: {', '.join(remaining)}")
        return 1

    print("VERDICT: the build is everything it can be without a certificate.")
    print("         If antivirus still flags it, that is the expected case")
    print("         for an unsigned tool that hooks the keyboard and")
    print("         captures the screen. Sign it, and submit the false")
    print("         positive - docs/ANTIVIRUS.md sections 4 and 5.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
