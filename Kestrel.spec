# -*- mode: python ; coding: utf-8 -*-
"""
Kestrel.spec
=========
Build a standalone Windows executable with PyInstaller.

    pyinstaller Kestrel.spec

Must be run ON WINDOWS - PyInstaller doesn't cross-compile, and this app is
Windows-only anyway (AppOpener, taskkill/tasklist, winsound, winreg-adjacent
bits). Produces dist/Kestrel/Kestrel.exe (--onedir: a folder, not a single file - startup is much faster than --onefile, which re-extracts everything to a
temp dir on every launch).

IMPORTANT: a .env file with GEMINI_API_KEY is NOT bundled (it's user-specific
and gitignored on purpose) - copy one next to Kestrel.exe after building, or
the app will start but every AI request will show the "no API key" message
(see ai/main.py's ai()).

Data files below mirror the source tree layout on purpose: paths.py's
BASE_PATH resolves to sys._MEIPASS / the exe's folder when frozen, and every
load_file()/uic.loadUi() call in the app still does e.g.
os.path.join(BASE_PATH, "ai", "instructions.txt") - so the bundled copies
need to live at those same relative paths for anything to find them.

── Antivirus false positives ────────────────────────────────────────
Several settings here exist to keep Defender and the SmartScreen prompt
off a build that has nothing wrong with it. Read docs/ANTIVIRUS.md
before changing any of them; the short version is:

  upx=False     UPX-packing an executable is one of the highest-weighted
                static heuristics there is, because real malware packs
                itself to defeat signature matching and almost nothing
                else does any more. It saves perhaps 30 MB and costs
                detections on engines that score packing alone.
  --onedir      Already the mode below. --onefile unpacks the whole app
                into %TEMP% on every launch and runs it from there,
                which is literally the dropper behaviour engines watch
                for at runtime.
  version=      An unsigned executable carrying no publisher, product
                name or description is scored as such. Generated below
                so it never drifts from paths.APP_VERSION.
  manifest=     Says explicitly that the app never asks for elevation
                and never uses uiAccess. See installer/Kestrel.manifest.

None of this substitutes for an Authenticode signature, which is the
only thing that actually fixes "Unknown publisher". docs/ANTIVIRUS.md
covers what is fixable here and what is not.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH is injected by PyInstaller and is the directory holding this
# file. Importing the project's own branding/paths modules from it means
# the version resource below is generated from the same constants the
# running app uses, rather than a third copy of the version number to
# keep in sync by hand (installer/Kestrel.iss is the second, and says so).
PROJECT_ROOT = SPECPATH if "SPECPATH" in globals() else os.path.abspath(".")
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import branding
from paths import APP_VERSION

# No `cipher=` / bytecode encryption. PyInstaller removed the feature in
# 6.0, and it was never wanted here anyway: encrypted bytecode inside an
# executable is an obfuscation heuristic in its own right, and this app
# is GPL v3 (see LICENSE), so there is no source to hide. The same
# argument rules out PyArmor and friends - docs/ANTIVIRUS.md explains
# why obfuscating the build makes the detection problem worse rather
# than better.

hidden_imports = (
    # google-genai occasionally needs submodules PyInstaller's static
    # analysis doesn't discover on its own (dynamic imports internally).
    collect_submodules("google.genai")
    + collect_submodules("google.auth")
    # pyautogui's own dependency stack sometimes gets missed too.
    + ["pymsgbox", "pyscreeze", "pytweening", "pygetwindow", "mouseinfo"]
)

datas = [
    ("app/ui", "app/ui"),                                   # .ui files, loaded at runtime via uic.loadUi
    ("assets", "assets"),                                    # icons/svgs
    ("ai/instructions.txt", "ai"),                            # main chat system prompt
    ("ai/autonomous_instructions.txt", "ai"),                 # autonomous mode system prompt
    ("ai/generalizer_instructions.txt", "ai"),                # macro-generalizer sub-agent system prompt (ai/macro_generalizer.py) - missing here meant a frozen build's "Generalize this macro" button would fail outright, since it's loaded from disk at runtime like the other two .txt files above, not baked into any .py source
    ("app/locale", "app/locale"),                             # i18n catalogs (app/i18n.py), loaded from disk at runtime for the same reason
]

# Modules nothing in the app imports, kept out of the bundle. This is
# deliberately short. A long excludes list is a good way to produce a
# build that works on the machine it was built on and fails on someone
# else's, and the size saving is not the point here: the point is that
# every DLL in dist/ is one more thing a scanner looks at, and a test
# framework or a plotting library has no business in a shipped desktop
# app. tkinter is NOT excluded - pymsgbox imports it, and pyautogui
# imports pymsgbox.
excludes = [
    "pytest", "_pytest", "py", "pluggy",
    "IPython", "jupyter", "notebook", "ipykernel",
    "matplotlib", "pandas", "scipy",
    "pydoc_data", "lib2to3",
]


def _version_resource() -> str:
    """
    Write a PyInstaller version file next to the build and return its
    path, so Kestrel.exe carries a real VERSIONINFO resource: Publisher,
    Product name, Description, version, copyright.

    Generated rather than checked in, because the version number lives in
    paths.APP_VERSION and a second hand-maintained copy would eventually
    disagree with it. The file itself is a Python literal, which is the
    format PyInstaller's own --version-file expects.
    """
    parts = []
    for chunk in APP_VERSION.strip().lstrip("vV").split("-")[0].split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    while len(parts) < 4:
        parts.append(0)
    quad = tuple(parts[:4])
    readable = ".".join(str(n) for n in quad)

    text = f'''# Auto-generated by Kestrel.spec from paths.APP_VERSION. Do not edit.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={quad},
    prodvers={quad},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        "040904B0",
        [StringStruct("CompanyName", {branding.PUBLISHER!r}),
         StringStruct("FileDescription", {branding.PRODUCT_DESCRIPTION!r}),
         StringStruct("FileVersion", {readable!r}),
         StringStruct("InternalName", {branding.APP_NAME!r}),
         StringStruct("LegalCopyright", {branding.COPYRIGHT!r}),
         StringStruct("OriginalFilename", {branding.APP_NAME + ".exe"!r}),
         StringStruct("ProductName", {branding.APP_NAME!r}),
         StringStruct("ProductVersion", {readable!r})])
    ]),
    # 0x409 = en-US, 1200 = Unicode. Must agree with the "040904B0" key
    # above or Explorer's Details tab shows the fields blank.
    VarFileInfo([VarStruct("Translation", [0x409, 1200])])
  ]
)
'''
    out = os.path.join(PROJECT_ROOT, "build", "version_info.txt")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    return out


VERSION_FILE = _version_resource()
MANIFEST_FILE = os.path.join(PROJECT_ROOT, "installer", "Kestrel.manifest")
# Runs before PyInstaller's own runtime hooks (and so before PyQt6 loads
# Qt): picks the newest Microsoft C++ runtime on the machine for the whole
# process. See app/win_runtime.py.
RUNTIME_HOOKS = [os.path.join(PROJECT_ROOT, "installer", "pyi_rth_kestrel_runtime.py")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=RUNTIME_HOOKS,
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=branding.APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # See this file's "Antivirus false positives" note. Do not set this
    # back to True to save disk space.
    upx=False,
    console=False,       # GUI app - output goes to the in-app Live Console, not a terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    # Set to your Developer ID for a macOS build. Windows signing is a
    # separate signtool call after this runs - see docs/ANTIVIRUS.md.
    codesign_identity=None,
    entitlements_file=None,
    icon=branding.WINDOWS_ICO_PATH,
    version=VERSION_FILE,
    manifest=MANIFEST_FILE,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,           # same reason as EXE above
    upx_exclude=[],
    name=branding.APP_NAME,
)
