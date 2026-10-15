# Kestrel - AI desktop automation for Windows.
# Copyright (C) 2026 the Kestrel authors
# Dual-licensed: GNU GPL v3 (see LICENSE), or a commercial licence.
# This program comes with ABSOLUTELY NO WARRANTY.

"""
installer/setup/build.py
========================
Compiles the setup stub (installer/bin/KestrelSetup-stub.exe) from the C
sources in this folder. You only need this after changing those sources;
build_installer.py uses the committed stub otherwise.

The stub links no C runtime, only Windows system DLLs, so any of these
toolchains produces a self-contained executable:

  --toolchain msvc          cl.exe + rc.exe, from an "x64 Native Tools
                            Command Prompt for VS" (Build Tools are enough)
  --toolchain zig           zig, e.g. `pip install ziglang`
  --toolchain clang-mingw   clang + lld + llvm-windres, with a mingw-w64
                            sysroot (--sysroot, or KS_MINGW_SYSROOT) that
                            has include/ and lib/lib*.a import libraries.
                            Works from Linux or macOS too.

Without --toolchain, the first one available is used, in that order.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(os.path.dirname(HERE), "bin", "KestrelSetup-stub.exe")

SOURCES = [
    "win_main.c", "win_util.c", "ui.c", "gfx.c", "text_gdi.c", "payload.c", "assets.c", "util.c",
    "xz/xz_dec_stream.c", "xz/xz_dec_lzma2.c", "xz/xz_dec_bcj.c", "xz/xz_crc32.c", "xz/xz_crc64.c",
]
LIBS = ["kernel32", "user32", "gdi32", "shell32", "ole32", "advapi32", "dwmapi"]
DEFINES = ["UNICODE", "_UNICODE", "WIN32_LEAN_AND_MEAN", "NDEBUG"]


def run(cmd: list[str], cwd: str = HERE) -> None:
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def build_msvc(out: str) -> None:
    tmp = tempfile.mkdtemp(prefix="ks-stub-")
    try:
        res = os.path.join(tmp, "setup.res")
        run(["rc", "/nologo", "/fo", res, "setup.rc"])
        run(["cl", "/nologo", "/c", "/O2", "/GS-", "/Gs1000000", "/W3", "/std:c11", "/utf-8", "/Zl"]
            + [f"/D{d}" for d in DEFINES] + [f"/Fo{tmp}\\"] + SOURCES)
        objs = glob.glob(os.path.join(tmp, "*.obj"))
        run(["link", "/nologo", "/NODEFAULTLIB", "/ENTRY:ks_entry", "/SUBSYSTEM:WINDOWS", "/OPT:REF", "/OPT:ICF",
             "/DYNAMICBASE", "/NXCOMPAT", "/HIGHENTROPYVA", "/MANIFEST:NO", f"/OUT:{out}", res] + objs
            + [f"{lib}.lib" for lib in LIBS])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _zig() -> list[str] | None:
    for cand in (["zig"], [sys.executable, "-m", "ziglang"]):
        try:
            subprocess.run(cand + ["version"], capture_output=True, check=True)
            return cand
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def build_zig(out: str) -> None:
    zig = _zig()
    tmp = tempfile.mkdtemp(prefix="ks-stub-")
    try:
        res = os.path.join(tmp, "setup.res")
        run(zig + ["rc", "/fo", res, "setup.rc"])
        run(zig + ["cc", "-target", "x86_64-windows-gnu", "-O2", "-std=c11", "-ffreestanding", "-fno-stack-protector",
                   "-nostdlib", "-Wl,--subsystem,windows", "-Wl,-e,ks_entry", "-o", out, res]
            + [f"-D{d}" for d in DEFINES] + SOURCES + [f"-l{lib}" for lib in LIBS])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_clang_mingw(out: str, sysroot: str) -> None:
    include = os.path.join(sysroot, "include")
    libdir = os.path.join(sysroot, "lib")
    resource_dir = subprocess.run(["clang", "-print-resource-dir"], capture_output=True, text=True, check=True).stdout.strip()
    tmp = tempfile.mkdtemp(prefix="ks-stub-")
    try:
        res = os.path.join(tmp, "setup_res.o")
        windres = shutil.which("llvm-windres") or shutil.which("x86_64-w64-mingw32-windres")
        if not windres and shutil.which("llvm-config"):
            bindir = subprocess.run(["llvm-config", "--bindir"], capture_output=True, text=True).stdout.strip()
            cand = os.path.join(bindir, "llvm-windres")
            windres = cand if os.path.exists(cand) else None
        if not windres:
            raise SystemExit("llvm-windres (or x86_64-w64-mingw32-windres) not found")
        run([windres, "--target=pe-x86-64" if "mingw32" in windres else "--target=x86_64-w64-mingw32",
             "-I", include, "-i", "setup.rc", "-O", "coff", "-o", res])
        run(["clang", "--target=x86_64-w64-mingw32", "-O2", "-std=c11", "-ffreestanding", "-fno-stack-protector",
             "-nostdinc", "-isystem", os.path.join(resource_dir, "include"), "-isystem", include,
             "-Wall", "-Wextra", "-Wno-unused-parameter", "-Wno-missing-field-initializers",
             "-Wframe-larger-than=4000",
             "-nostdlib", "-fuse-ld=lld", "-Wl,--subsystem,windows:6.1", "-Wl,-e,ks_entry",
             "-Wl,--dynamicbase", "-Wl,--nxcompat", "-Wl,--high-entropy-va", "-s",
             "-L", libdir, "-o", out, res]
            + [f"-D{d}" for d in DEFINES] + SOURCES + [f"-l{lib}" for lib in LIBS])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--toolchain", choices=["msvc", "zig", "clang-mingw"])
    ap.add_argument("--sysroot", default=os.environ.get("KS_MINGW_SYSROOT"))
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    tc = args.toolchain
    if not tc:
        if shutil.which("cl") and shutil.which("rc"):
            tc = "msvc"
        elif _zig():
            tc = "zig"
        elif shutil.which("clang") and args.sysroot:
            tc = "clang-mingw"
        else:
            raise SystemExit("No toolchain found. Use an x64 Native Tools prompt (MSVC), `pip install ziglang`, "
                             "or clang with --sysroot pointing at a mingw-w64 sysroot.")
    if tc == "msvc":
        build_msvc(out)
    elif tc == "zig":
        build_zig(out)
    else:
        if not args.sysroot:
            raise SystemExit("clang-mingw needs --sysroot (or KS_MINGW_SYSROOT)")
        build_clang_mingw(out, os.path.abspath(args.sysroot))
    print(f"built {out} ({os.path.getsize(out) // 1024} KB)")


if __name__ == "__main__":
    main()
