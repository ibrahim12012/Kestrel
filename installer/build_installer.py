# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
installer/build_installer.py
============================
Builds KestrelSetup.exe from PyInstaller's output.

    pyinstaller Kestrel.spec
    python installer/build_installer.py

produces dist/KestrelSetup-<version>.exe. That file is the setup stub
(installer/bin/KestrelSetup-stub.exe, built from installer/setup/*.c) with
the app appended to it as a compressed payload. See installer/README.md.

Only the Python standard library is needed. Other commands:

    python installer/build_installer.py stub      rebuild the stub from source
    python installer/build_installer.py assets    regenerate setup/logo_data.h
    python installer/build_installer.py verify F  check a built setup's payload

Nothing here is specific to Windows except the optional signing step, so
the payload can be built (and tested) anywhere Python runs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib.util
import io
import lzma
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
SETUP_SRC = os.path.join(HERE, "setup")
DEFAULT_STUB = os.path.join(HERE, "bin", "KestrelSetup-stub.exe")

PAYLOAD_MAGIC = b"KSTPAYLD"
PAYLOAD_VERSION = 1
HEADER_SIZE = 64

# Uncompressed bytes per independently compressed chunk. The installer
# decompresses chunks on several threads at once, so more (smaller) chunks
# install faster on a multi-core machine; fewer (larger) ones compress a
# little better. 24 MB keeps the size cost to a couple of percent.
CHUNK_TARGET = 24 * 1024 * 1024

# Files whose contents are x86-64 machine code. Chunks made of these get
# the BCJ filter, which rewrites relative call/jump targets into absolute
# ones so the same function called from many places compresses as one.
EXECUTABLE_SUFFIXES = {".exe", ".dll", ".pyd", ".so", ".node"}


# ── Project metadata ────────────────────────────────────────────────────

def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def project_metadata() -> dict:
    """Everything the installer needs to know about the app, from the same
    constants the app itself uses (branding.py, paths.APP_VERSION)."""
    sys.path.insert(0, PROJECT_ROOT)
    try:
        branding = _load_module("branding", os.path.join(PROJECT_ROOT, "branding.py"))
        # paths.py imports branding and computes paths at import; reading
        # the version out of the source is simpler than importing it.
        with open(os.path.join(PROJECT_ROOT, "paths.py"), encoding="utf-8") as f:
            match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', f.read(), re.M)
    finally:
        sys.path.remove(PROJECT_ROOT)
    if not match:
        raise SystemExit("Could not find APP_VERSION in paths.py")
    return {
        "app_name": branding.APP_NAME,
        "app_version": match.group(1),
        "publisher": branding.PUBLISHER,
        "description": branding.PRODUCT_DESCRIPTION,
        # Apps & Features key, under HKCU. Kept stable across versions.
        "app_id": branding.APP_NAME,
        "exe_name": branding.APP_NAME + ".exe",
        "aumid": branding.WINDOWS_APP_ID,
        "url_about": branding.DEFAULT_WEBSITE_URL,
        "url_help": branding.REPO_URL,
        "file_ext": branding.EXPORT_SUFFIX,
        "file_progid": branding.FILE_TYPE_PROGID,
        "file_type_name": branding.FILE_TYPE_NAME,
        # %LOCALAPPDATA%\<this> holds chats, macros and settings
        # (paths.USER_DATA_PATH). Offered for deletion on uninstall.
        "user_data_dir": branding.APP_NAME,
        # %LOCALAPPDATA%\Programs\<this> is the default install folder.
        "install_dir_name": branding.APP_NAME,
        "uninstaller_name": f"Uninstall {branding.APP_NAME}.exe",
        # The Inno Setup installer this replaces registered itself under
        # this key (installer/Kestrel.iss AppId). Setup removes such an
        # install before putting the new one in place.
        "legacy_uninstall_keys": "{4F1E9C2A-7B3D-4A6F-9E1C-1A2B3C4D5E6F}_is1",
        # How setup asks a running Kestrel to close before replacing its
        # files: it sets the named event Local\<this>, which the app
        # creates at startup and waits on (app/single_instance.py,
        # QUIT_EVENT_NAME). The key keeps its old name ("message") so the
        # uninstall data of an existing install still reads.
        "quit_message": "Kestrel.Installer.RequestQuit",
    }


# ── Licence text ────────────────────────────────────────────────────────

_SECTION_HEADING = re.compile(r"^\d+\.\s+[A-Z0-9][A-Z0-9 ,;:()'&/-]+$")


def eula_to_markup(text: str) -> str:
    """
    EULA.md is hard-wrapped plain text. The setup's licence viewer re-wraps
    it to its own width, so paragraphs are joined back into single lines
    and each line is tagged with what it is:

        T<tab>title   H<tab>heading   P<tab>paragraph   B<tab>bullet
    """
    blocks: list[tuple[str, str]] = []
    kind, parts = None, []

    def flush():
        nonlocal kind, parts
        if kind and parts:
            joined = ""
            for part in (p.strip() for p in parts if p.strip()):
                # "command-" at a line end + "line" is one hyphenated
                # word, not two words with a dash between them.
                if joined.endswith("-") and len(joined) > 1 and joined[-2].isalpha() and part[:1].isalpha():
                    joined += part
                else:
                    joined += (" " if joined else "") + part
            blocks.append((kind, joined))
        kind, parts = None, []

    lines = text.replace("\r\n", "\n").split("\n")
    for i, raw in enumerate(lines):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            # A blank line ends a paragraph; a Markdown rule ("---") only
            # separates, and the viewer already spaces paragraphs apart.
            flush()
            continue
        stripped = re.sub(r"^#+\s*", "", stripped)
        stripped = stripped.replace("**", "").replace("`", "")
        if not blocks and kind is None:
            kind, parts = "T", [stripped]
            flush()
            continue
        if _SECTION_HEADING.match(stripped):
            flush()
            blocks.append(("H", stripped))
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            flush()
            kind, parts = "B", [bullet.group(1)]
            continue
        if kind == "B" and (line.startswith("    ") or line.startswith("\t")):
            parts.append(stripped)
            continue
        if kind == "B":
            flush()
        if kind is None:
            kind = "P"
        parts.append(stripped)
    flush()
    return "\n".join(f"{k}\t{t}" for k, t in blocks) + "\n"


# ── Logo ────────────────────────────────────────────────────────────────

def _read_png_rgba(path: str) -> tuple[int, int, bytes]:
    """Minimal PNG reader (8-bit RGBA / RGB, non-interlaced), so building
    the assets needs nothing beyond the standard library."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    pos, idat, width = 8, b"", 0
    height = bit_depth = color_type = interlace = 0
    while pos < len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
    if bit_depth != 8 or color_type not in (2, 6) or interlace:
        raise ValueError(f"{path}: only 8-bit RGB/RGBA non-interlaced PNGs are supported")
    channels = 4 if color_type == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray(width * height * 4)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        ftype = raw[p]
        row = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for x in range(stride):
            a = row[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            if ftype == 1:
                row[x] = (row[x] + a) & 0xFF
            elif ftype == 2:
                row[x] = (row[x] + b) & 0xFF
            elif ftype == 3:
                row[x] = (row[x] + ((a + b) >> 1)) & 0xFF
            elif ftype == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                row[x] = (row[x] + pred) & 0xFF
        for x in range(width):
            o = (y * width + x) * 4
            s = x * channels
            out[o:o + 3] = row[s:s + 3]
            out[o + 3] = row[s + 3] if channels == 4 else 255
        prev = row
    return width, height, bytes(out)


def _box_downscale(w: int, h: int, rgba: bytes, box: tuple[int, int, int, int], size: int) -> bytes:
    """Crop to `box` and area-average down to size x size, in premultiplied
    space (so transparent pixels do not bleed dark fringes)."""
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0, y1 - y0
    out = bytearray(size * size * 4)
    for oy in range(size):
        sy0, sy1 = y0 + oy * ch / size, y0 + (oy + 1) * ch / size
        for ox in range(size):
            sx0, sx1 = x0 + ox * cw / size, x0 + (ox + 1) * cw / size
            acc = [0.0, 0.0, 0.0, 0.0]
            wsum = 0.0
            for sy in range(int(sy0), min(int(sy1 + 0.999), h)):
                wy = min(sy + 1, sy1) - max(sy, sy0)
                if wy <= 0:
                    continue
                for sx in range(int(sx0), min(int(sx1 + 0.999), w)):
                    wx = min(sx + 1, sx1) - max(sx, sx0)
                    if wx <= 0:
                        continue
                    i = (sy * w + sx) * 4
                    a = rgba[i + 3] / 255.0
                    wgt = wx * wy
                    acc[0] += rgba[i] * a * wgt
                    acc[1] += rgba[i + 1] * a * wgt
                    acc[2] += rgba[i + 2] * a * wgt
                    acc[3] += rgba[i + 3] * wgt
                    wsum += wgt
            o = (oy * size + ox) * 4
            if wsum > 0:
                r, g, b, a = (v / wsum for v in acc)
                # BGRA, premultiplied: what the setup draws with.
                out[o] = min(255, int(b + 0.5))
                out[o + 1] = min(255, int(g + 0.5))
                out[o + 2] = min(255, int(r + 0.5))
                out[o + 3] = min(255, int(a + 0.5))
    return bytes(out)


def build_assets() -> None:
    """Regenerate installer/setup/logo_data.h from the app icon."""
    sys.path.insert(0, PROJECT_ROOT)
    try:
        branding = _load_module("branding", os.path.join(PROJECT_ROOT, "branding.py"))
    finally:
        sys.path.remove(PROJECT_ROOT)
    png = os.path.join(PROJECT_ROOT, branding.ICON_PATH)
    w, h, rgba = _read_png_rgba(png)
    # Tight box around the mark, with a little air.
    xs = [x for y in range(0, h, 4) for x in range(0, w, 4) if rgba[(y * w + x) * 4 + 3] > 8]
    ys = [y for y in range(0, h, 4) for x in range(0, w, 4) if rgba[(y * w + x) * 4 + 3] > 8]
    x0, x1, y0, y1 = min(xs), max(xs) + 4, min(ys), max(ys) + 4
    side = max(x1 - x0, y1 - y0)
    pad = side * 0.02
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = side / 2 + pad
    box = (int(max(0, cx - half)), int(max(0, cy - half)), int(min(w, cx + half)), int(min(h, cy + half)))
    size = 256
    print(f"[assets] {os.path.relpath(png, PROJECT_ROOT)} -> {size}x{size} logo")
    pixels = _box_downscale(w, h, rgba, box, size)
    packed = lzma.compress(pixels, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64,
                           filters=[{"id": lzma.FILTER_LZMA2, "preset": 9, "dict_size": 1 << 20}])
    lines = [
        "/* Generated by installer/build_installer.py assets. Do not edit. */",
        f"/* Source: {branding.ICON_PATH}, premultiplied BGRA, xz-compressed. */",
        "#ifndef KS_LOGO_DATA_H",
        "#define KS_LOGO_DATA_H",
        f"#define KS_LOGO_SIZE {size}",
        f"static const unsigned char k_logo_xz[{len(packed)}] = {{",
    ]
    for i in range(0, len(packed), 20):
        lines.append("    " + ", ".join(str(b) for b in packed[i:i + 20]) + ",")
    lines += ["};", "#endif", ""]
    out = os.path.join(SETUP_SRC, "logo_data.h")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"[assets] wrote {os.path.relpath(out, PROJECT_ROOT)} ({len(packed)} bytes compressed)")




# ── Payload ─────────────────────────────────────────────────────────────

def _lzma2_dict_size(n: int) -> int:
    """Smallest LZMA2-representable dictionary size (2^k or 3*2^(k-1)) that
    covers n bytes, within 64 KB .. 64 MB. The decoder allocates exactly
    this much per thread, so it should not be larger than the chunk."""
    size = 1 << 16
    while size < n and size < (64 << 20):
        mid = size + size // 2
        if mid >= n:
            return mid
        size <<= 1
    return min(size, 64 << 20)


def collect_files(dist_dir: str, extra: list[tuple[str, str]]) -> tuple[list[str], list[tuple[str, str, int]]]:
    """(dirs, files) under dist_dir, as payload-relative '/' paths.
    `extra` adds (relative path, source path) pairs, e.g. the uninstaller."""
    files, dirs = [], set()
    for root, dnames, fnames in os.walk(dist_dir):
        dnames.sort()
        for name in sorted(fnames):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, dist_dir).replace(os.sep, "/")
            files.append((rel, full, os.path.getsize(full)))
    for rel, full in extra:
        files.append((rel, full, os.path.getsize(full)))
    for rel, _, _ in files:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    # Parents before children.
    return sorted(dirs, key=lambda d: (d.count("/"), d)), files


def plan_chunks(files: list[tuple[str, str, int]]) -> list[list[tuple[str, str, int]]]:
    """Group files into independently compressed chunks. Machine code goes
    in chunks of its own (they get the x86 filter), and similar files are
    kept together, which is what lets the compressor find the repeats."""
    def key(f):
        rel = f[0]
        ext = os.path.splitext(rel)[1].lower()
        return (0 if ext in EXECUTABLE_SUFFIXES else 1, ext, os.path.dirname(rel), rel)

    chunks, current, current_size, current_kind = [], [], 0, None
    for f in sorted(files, key=key):
        kind = key(f)[0]
        if current and (current_size + f[2] > CHUNK_TARGET or kind != current_kind):
            chunks.append(current)
            current, current_size = [], 0
        current.append(f)
        current_size += f[2]
        current_kind = kind
    if current:
        chunks.append(current)
    return chunks


def _compress_chunk(job: tuple[list[str], bool, int]) -> tuple[bytes, list[int]]:
    paths, executable, preset = job
    data = bytearray()
    crcs = []
    for path in paths:
        with open(path, "rb") as f:
            blob = f.read()
        crcs.append(zlib.crc32(blob) & 0xFFFFFFFF)
        data += blob
    filters = []
    if executable:
        filters.append({"id": lzma.FILTER_X86})
    filters.append({"id": lzma.FILTER_LZMA2, "preset": preset, "dict_size": _lzma2_dict_size(len(data))})
    packed = lzma.compress(bytes(data), format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, filters=filters)
    return packed, crcs


def _pack_meta(meta: dict) -> bytes:
    out = bytearray()
    for k, v in meta.items():
        kb, vb = k.encode("utf-8"), str(v).encode("utf-8")
        out += struct.pack("<H", len(kb)) + kb + struct.pack("<I", len(vb)) + vb
    return bytes(out)


def build_payload(meta: dict, dirs: list[str], chunks: list[list[tuple[str, str, int]]],
                  preset: int, workers: int | None) -> bytes:
    jobs = [([f[1] for f in chunk], os.path.splitext(chunk[0][0])[1].lower() in EXECUTABLE_SUFFIXES, preset)
            for chunk in chunks]
    started = time.time()
    total = sum(f[2] for c in chunks for f in c)
    print(f"[pack] compressing {sum(len(c) for c in chunks)} files, {total / 1048576:.1f} MB, "
          f"in {len(chunks)} chunks")
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_compress_chunk, jobs))
    packed_total = sum(len(r[0]) for r in results)
    print(f"[pack] {total / 1048576:.1f} MB -> {packed_total / 1048576:.1f} MB "
          f"({100.0 * packed_total / max(total, 1):.1f}%) in {time.time() - started:.1f} s")

    meta_bytes = _pack_meta(meta)

    # The index has fixed-width offsets, so its size is known before the
    # offsets are.
    def index_bytes(chunk_offsets: list[int]) -> bytes:
        out = bytearray(struct.pack("<I", len(dirs)))
        for d in dirs:
            db = d.encode("utf-8")
            out += struct.pack("<H", len(db)) + db
        out += struct.pack("<I", sum(len(c) for c in chunks))
        for ci, (chunk, (_, crcs)) in enumerate(zip(chunks, results)):
            for (rel, _, size), crc in zip(chunk, crcs):
                rb = rel.encode("utf-8")
                out += struct.pack("<H", len(rb)) + rb + struct.pack("<QII", size, crc, ci)
        out += struct.pack("<I", len(chunks))
        first = 0
        for ci, chunk in enumerate(chunks):
            unpacked = sum(f[2] for f in chunk)
            out += struct.pack("<QQQII", chunk_offsets[ci], len(results[ci][0]), unpacked, first, len(chunk))
            first += len(chunk)
        return bytes(out)

    probe = index_bytes([0] * len(chunks))
    data_start = HEADER_SIZE + len(meta_bytes) + len(probe)
    offsets, pos = [], data_start
    for packed, _ in results:
        offsets.append(pos)
        pos += len(packed)
    index = index_bytes(offsets)
    assert len(index) == len(probe)

    header = bytearray(HEADER_SIZE)
    header[0:8] = PAYLOAD_MAGIC
    struct.pack_into("<II", header, 8, PAYLOAD_VERSION, HEADER_SIZE)
    struct.pack_into("<QII", header, 16, HEADER_SIZE, len(meta_bytes), zlib.crc32(meta_bytes) & 0xFFFFFFFF)
    struct.pack_into("<QII", header, 32, HEADER_SIZE + len(meta_bytes), len(index), zlib.crc32(index) & 0xFFFFFFFF)
    struct.pack_into("<QI", header, 48, total, 0)
    struct.pack_into("<I", header, 60, zlib.crc32(bytes(header[:60])) & 0xFFFFFFFF)
    return bytes(header) + meta_bytes + index + b"".join(r[0] for r in results)


# ── The stub ────────────────────────────────────────────────────────────

def pe_image_end(data: bytes) -> int:
    """Where the PE image's last section ends; the payload goes there."""
    if data[:2] != b"MZ":
        raise ValueError("not a Windows executable")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE file")
    sections, = struct.unpack_from("<H", data, pe + 6)
    opt_size, = struct.unpack_from("<H", data, pe + 20)
    table = pe + 24 + opt_size
    end = 0
    for i in range(sections):
        raw_size, raw_ptr = struct.unpack_from("<II", data, table + i * 40 + 16)
        if raw_size:
            end = max(end, raw_ptr + raw_size)
    return end


VERSION_SLOT = "@@KS_VERSION_SLOT@@@@@@@@@@@@@"  # must match setup.rc


def stamp_version(stub: bytes, version: str) -> bytes:
    """Write the app version into the stub's VERSIONINFO resource, so
    Explorer's Properties shows the version being installed. The stub is
    built once with fixed-width placeholders; this fills them in place."""
    data = bytearray(stub)
    nums = [int(x) for x in re.findall(r"\d+", version)[:4]] + [0, 0, 0, 0]
    ms, ls = (nums[0] << 16) | nums[1], (nums[2] << 16) | nums[3]
    sig = data.find(struct.pack("<I", 0xFEEF04BD))
    if sig < 0:
        print("[pack] note: stub has no VERSIONINFO; skipping version stamp")
        return stub
    struct.pack_into("<IIII", data, sig + 8, ms, ls, ms, ls)
    slot = VERSION_SLOT.encode("utf-16-le")
    value = version[:len(VERSION_SLOT)].encode("utf-16-le")
    value = value + b"\0" * (len(slot) - len(value))
    count = 0
    pos = data.find(slot)
    while pos >= 0:
        data[pos:pos + len(slot)] = value
        count += 1
        pos = data.find(slot, pos + len(slot))
    if count == 0:
        print("[pack] note: stub has no version string slots; only the numeric version was stamped")
    return bytes(data)


def _find_compiler() -> list[str] | None:
    for cand in (["clang"], ["zig", "cc"], [sys.executable, "-m", "ziglang", "cc"]):
        try:
            subprocess.run(cand + ["--version"], capture_output=True, check=True)
            return cand
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def build_stub(out_path: str = DEFAULT_STUB) -> None:
    """Compile the setup stub from installer/setup/. Needs clang or zig
    (pip install ziglang); both bring their own Windows headers only when
    targeting mingw, so clang also needs a mingw-w64 sysroot on PATH.
    See installer/setup/build.cmd for the MSVC equivalent."""
    script = os.path.join(SETUP_SRC, "build.py")
    subprocess.run([sys.executable, script, "--out", out_path], check=True)


# ── Build ───────────────────────────────────────────────────────────────

def _sign(path: str, command: str | None) -> None:
    if not command:
        return
    cmd = [part.replace("{file}", path) for part in shlex.split(command, posix=(os.name != "nt"))]
    if not any(path in part for part in cmd):
        cmd.append(path)
    print(f"[sign] {os.path.basename(path)}")
    subprocess.run(cmd, check=True)


def build(args) -> str:
    meta = project_metadata()
    dist = os.path.abspath(args.dist or os.path.join(PROJECT_ROOT, "dist", meta["app_name"]))
    exe = os.path.join(dist, meta["exe_name"])
    if not os.path.isfile(exe):
        raise SystemExit(f"{exe} not found. Run `pyinstaller Kestrel.spec` first, or pass --dist.")
    stub_path = os.path.abspath(args.stub or DEFAULT_STUB)
    if args.rebuild_stub or not os.path.isfile(stub_path):
        build_stub(stub_path)
    with open(stub_path, "rb") as f:
        stub = f.read()
    end = pe_image_end(stub)
    if end != len(stub):
        raise SystemExit(f"{stub_path}: expected a plain executable ({end} bytes of image, {len(stub)} in file). "
                         "Use an unsigned, freshly built stub; sign the finished setup instead.")
    stub = stamp_version(stub, meta["app_version"])

    with open(os.path.join(PROJECT_ROOT, "EULA.md"), encoding="utf-8") as f:
        meta["license"] = eula_to_markup(f.read())

    tmp = tempfile.mkdtemp(prefix="kestrel-setup-")
    try:
        uninstaller = os.path.join(tmp, meta["uninstaller_name"])
        with open(uninstaller, "wb") as f:
            f.write(stub)
        _sign(uninstaller, args.sign)

        dirs, files = collect_files(dist, [(meta["uninstaller_name"], uninstaller)])
        total = sum(f[2] for f in files)
        meta["estimated_size_kb"] = str((total + 1023) // 1024)
        chunks = plan_chunks(files)
        preset = 6 if args.fast else (9 | lzma.PRESET_EXTREME)
        payload = build_payload(meta, dirs, chunks, preset, args.jobs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    out = os.path.abspath(args.out or os.path.join(PROJECT_ROOT, "dist",
                                                    f"{meta['app_name']}Setup-{meta['app_version']}.exe"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(stub)
        f.write(payload)
    _sign(out, args.sign)
    verify(out, deep=not args.fast)
    print(f"[done] {out} ({os.path.getsize(out) / 1048576:.1f} MB)")
    return out


def verify(path: str, deep: bool = True) -> None:
    """Re-read a built setup the way the installer will, and (deep) unpack
    every chunk and check every file's CRC."""
    with open(path, "rb") as f:
        data = f.read()
    base = pe_image_end(data)
    header = data[base:base + HEADER_SIZE]
    if header[:8] != PAYLOAD_MAGIC:
        raise SystemExit(f"{path}: no payload found")
    if struct.unpack_from("<I", header, 60)[0] != zlib.crc32(header[:60]) & 0xFFFFFFFF:
        raise SystemExit(f"{path}: payload header damaged")
    meta_off, meta_size, meta_crc = struct.unpack_from("<QII", header, 16)
    idx_off, idx_size, idx_crc = struct.unpack_from("<QII", header, 32)
    total, = struct.unpack_from("<Q", header, 48)
    meta = data[base + meta_off:base + meta_off + meta_size]
    index = data[base + idx_off:base + idx_off + idx_size]
    if zlib.crc32(meta) & 0xFFFFFFFF != meta_crc or zlib.crc32(index) & 0xFFFFFFFF != idx_crc:
        raise SystemExit(f"{path}: payload metadata damaged")
    p = 0
    ndirs, = struct.unpack_from("<I", index, p); p += 4
    for _ in range(ndirs):
        n, = struct.unpack_from("<H", index, p); p += 2 + n
    nfiles, = struct.unpack_from("<I", index, p); p += 4
    files = []
    for _ in range(nfiles):
        n, = struct.unpack_from("<H", index, p); p += 2
        rel = index[p:p + n].decode("utf-8"); p += n
        size, crc, chunk = struct.unpack_from("<QII", index, p); p += 16
        files.append((rel, size, crc, chunk))
    nchunks, = struct.unpack_from("<I", index, p); p += 4
    chunks = []
    for _ in range(nchunks):
        chunks.append(struct.unpack_from("<QQQII", index, p)); p += 32
    if p != len(index):
        raise SystemExit(f"{path}: index has trailing bytes")
    print(f"[verify] {nfiles} files in {nchunks} chunks, {total / 1048576:.1f} MB unpacked")
    if not deep:
        return
    for off, packed, unpacked, first, count in chunks:
        blob = lzma.decompress(data[base + off:base + off + packed], format=lzma.FORMAT_XZ)
        if len(blob) != unpacked:
            raise SystemExit(f"{path}: chunk size mismatch")
        pos = 0
        for rel, size, crc, _ in files[first:first + count]:
            if zlib.crc32(blob[pos:pos + size]) & 0xFFFFFFFF != crc:
                raise SystemExit(f"{path}: {rel} does not match its checksum")
            pos += size
    print("[verify] every file unpacks and matches its checksum")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build KestrelSetup.exe from PyInstaller's dist folder.")
    parser.add_argument("command", nargs="?", default="build", choices=["build", "stub", "assets", "verify"])
    parser.add_argument("path", nargs="?", help="for verify: the setup file to check")
    parser.add_argument("--dist", help="PyInstaller output folder (default: dist/Kestrel)")
    parser.add_argument("--out", help="output setup file (default: dist/KestrelSetup-<version>.exe)")
    parser.add_argument("--stub", help=f"setup stub to use (default: {os.path.relpath(DEFAULT_STUB, PROJECT_ROOT)})")
    parser.add_argument("--rebuild-stub", action="store_true", help="compile the stub from source first")
    parser.add_argument("--sign", metavar="COMMAND",
                        help='code-signing command, {file} is replaced by the file to sign, e.g. '
                             '"signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 {file}"')
    parser.add_argument("--fast", action="store_true", help="quicker, larger compression, for testing")
    parser.add_argument("--jobs", type=int, default=None, help="compression processes (default: all cores)")
    args = parser.parse_args(argv)

    if args.command == "assets":
        build_assets()
    elif args.command == "stub":
        build_stub(os.path.abspath(args.stub or DEFAULT_STUB))
    elif args.command == "verify":
        if not args.path:
            parser.error("verify needs the setup file")
        verify(args.path)
    else:
        build(args)


if __name__ == "__main__":
    main()
