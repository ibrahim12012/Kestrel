# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
End-to-end test of the setup payload: build_installer.py packs a tree,
the installer's own C extractor (payload.c, compiled for Linux as
extract_test) unpacks it, and the result must match byte for byte.
Also checks that damaged and hostile payloads are refused.

    python installer/setup/tests/test_payload.py
"""

import hashlib
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SETUP = os.path.dirname(HERE)
INSTALLER = os.path.dirname(SETUP)
sys.path.insert(0, INSTALLER)

import build_installer as bi  # noqa: E402

SOURCES = ["tests/extract_test.c", "payload.c", "util.c", "xz/xz_dec_stream.c", "xz/xz_dec_lzma2.c",
           "xz/xz_dec_bcj.c", "xz/xz_crc32.c", "xz/xz_crc64.c"]


def compile_extractor(out_dir):
    exe = os.path.join(out_dir, "extract_test")
    cmd = ["cc", "-O2", "-g", "-std=c11", "-D_GNU_SOURCE", "-Wall", "-Wextra", "-Wno-unused-parameter",
           "-fsanitize=address,undefined", "-o", exe] + [os.path.join(SETUP, s) for s in SOURCES] + ["-lpthread", "-lm"]
    subprocess.run(cmd, check=True)
    return exe


def fake_stub():
    """The smallest thing pe_image_end() accepts: MZ + PE headers, one
    section whose raw data ends exactly at the end of the file."""
    e_lfanew = 0x40
    opt_size = 0xF0
    table = e_lfanew + 24 + opt_size
    raw_ptr = 0x200
    raw_size = 0x200
    data = bytearray(raw_ptr + raw_size)
    data[0:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, e_lfanew)
    data[e_lfanew:e_lfanew + 4] = b"PE\0\0"
    struct.pack_into("<HH", data, e_lfanew + 4, 0x8664, 1)
    struct.pack_into("<H", data, e_lfanew + 20, opt_size)
    data[table:table + 8] = b".text\0\0\0"
    struct.pack_into("<II", data, table + 16, raw_size, raw_ptr)
    return bytes(data)


def make_tree(root):
    rng = random.Random(1234)
    files = {}

    def put(rel, blob):
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(blob)
        files[rel] = blob

    put("Kestrel.exe", fake_stub() + os.urandom(200_000))
    put("_internal/python311.dll", bytes(rng.getrandbits(8) for _ in range(300_000)))
    put("_internal/empty.txt", b"")
    put("_internal/also empty/zero.bin", b"")
    put("_internal/app/ui/main_window.ui", b"<ui>" + b"<widget/>" * 5000 + b"</ui>")
    put("_internal/assets/icon/caf\u00e9 \u2615.png", os.urandom(4096))
    # Bigger than one chunk, compressible.
    put("_internal/big/opengl32sw.dll", (b"\x48\x89\x5c\x24\x08" + os.urandom(3)) * (4 * 1024 * 1024))
    for i in range(400):
        put(f"_internal/lib/pkg{i % 17}/mod{i}.pyc", os.urandom(rng.randint(0, 3000)) + b"import os\n" * 50)
    return files


def build_setup(tree_root, out_path, extra_meta=None):
    dirs, files = bi.collect_files(tree_root, [])
    chunks = bi.plan_chunks(files)
    meta = {"app_name": "Kestrel", "app_version": "9.8.7", "license": "T\tTitle\nP\tBody\n"}
    meta.update(extra_meta or {})
    payload = bi.build_payload(meta, dirs, chunks, 6, None)
    with open(out_path, "wb") as f:
        f.write(fake_stub())
        f.write(payload)
    return chunks


def digest_tree(root):
    out = {}
    for base, _, names in os.walk(root):
        for n in names:
            p = os.path.join(base, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
    return out


def run(exe, setup, dest, threads=4):
    return subprocess.run([exe, setup, dest, str(threads)], capture_output=True, text=True)


def main():
    work = tempfile.mkdtemp(prefix="ks-payload-test-")
    try:
        exe = compile_extractor(work)
        src = os.path.join(work, "src")
        files = make_tree(src)
        setup = os.path.join(work, "setup.exe")
        chunks = build_setup(src, setup)
        assert len(chunks) >= 3, "test tree should span several chunks"
        bi.verify(setup)

        for threads in (1, 4, 16):
            dest = os.path.join(work, f"out{threads}")
            r = run(exe, setup, dest, threads)
            print(r.stdout.strip().splitlines()[-1])
            assert r.returncode == 0, r.stdout + r.stderr
            assert digest_tree(dest) == {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
            for d in ("_internal/also empty", "_internal/assets/icon"):
                assert os.path.isdir(os.path.join(dest, *d.split("/")))
        print("ok: extraction matches the source tree byte for byte, at 1, 4 and 16 threads")

        # A flipped byte inside a chunk must be caught.
        with open(setup, "rb") as f:
            data = bytearray(f.read())
        bad = bytearray(data)
        bad[len(bad) - 5000] ^= 0x55
        bad_path = os.path.join(work, "bad.exe")
        with open(bad_path, "wb") as f:
            f.write(bad)
        r = run(exe, bad_path, os.path.join(work, "outbad"))
        assert r.returncode != 0 and "damaged" in r.stdout, r.stdout
        print("ok: corrupted data is refused:", r.stdout.strip().splitlines()[-1])

        # A truncated download must be caught at open.
        trunc_path = os.path.join(work, "trunc.exe")
        with open(trunc_path, "wb") as f:
            f.write(data[:len(data) // 2])
        r = run(exe, trunc_path, os.path.join(work, "outtrunc"))
        assert r.returncode == 3 and "damaged" in r.stdout, r.stdout
        print("ok: truncated file is refused:", r.stdout.strip())

        # No payload at all (the uninstaller copy of the stub).
        bare = os.path.join(work, "bare.exe")
        with open(bare, "wb") as f:
            f.write(fake_stub())
        r = run(exe, bare, os.path.join(work, "outbare"))
        assert r.returncode == 3 and "no program" in r.stdout, r.stdout
        print("ok: stub without payload is recognised:", r.stdout.strip())

        # Paths that try to escape the install folder must be refused.
        for evil in ("../escape.txt", "a/../../b.txt", "C:/Windows/x.dll", "a\\b.txt", "a/./b.txt", "a//b"):
            evil_tree = [("x", os.path.join(src, "Kestrel.exe"), os.path.getsize(os.path.join(src, "Kestrel.exe")))]
            evil_tree[0] = (evil, evil_tree[0][1], evil_tree[0][2])
            payload = bi.build_payload({"app_name": "x"}, [], [evil_tree], 1, 1)
            ep = os.path.join(work, "evil.exe")
            with open(ep, "wb") as f:
                f.write(fake_stub() + payload)
            r = run(exe, ep, os.path.join(work, "outevil"))
            assert r.returncode == 3, (evil, r.stdout)
        print("ok: path traversal and odd paths are refused")
        print("ALL PAYLOAD TESTS PASSED")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
