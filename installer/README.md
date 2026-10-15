# Kestrel Setup

`KestrelSetup.exe` is a small Windows program written in C
(`installer/setup/`), with the app appended to it as a compressed
payload. It links no C runtime, only Windows system DLLs, and the stub is
about 150 KB before the payload.

## Building a setup

```bash
pyinstaller Kestrel.spec
python installer/build_installer.py
```

This writes `dist/KestrelSetup-<version>.exe`. It needs only the Python
standard library, so the payload can be built anywhere Python runs.

| Option | What it does |
|---|---|
| `--sign "signtool sign ... {file}"` | Signs the uninstaller and the setup (`{file}` is replaced) |
| `--fast` | Quicker, larger compression, for testing |
| `--jobs N` | Compression processes (default: all cores) |
| `--dist`, `--out`, `--stub` | Other input folder, output file, or stub |
| `--rebuild-stub` | Compiles the stub from source first |

`python installer/build_installer.py verify <setup.exe>` checks a built
setup's payload.

## The stub

`installer/bin/KestrelSetup-stub.exe` is committed, so a normal build
does not compile anything. Rebuild it after changing the C sources or
`assets/icon/Kestrel.ico`, which it embeds:

```bash
python installer/build_installer.py stub
```

or `python installer/setup/build.py --toolchain msvc | zig | clang-mingw`.
MSVC needs an "x64 Native Tools Command Prompt"; clang-mingw works from
Linux or macOS with a mingw-w64 sysroot (`--sysroot`).

The logo drawn in the setup window comes from
`assets/icon/Purple-transparent-1000.png`:
`python installer/build_installer.py assets` regenerates
`setup/logo_data.h` from it.

## What it does

- Installs per user, without administrator rights:
  `%LOCALAPPDATA%\Programs\Kestrel` for the app, `%LOCALAPPDATA%\Kestrel`
  for chats, macros and settings. Setup never touches the second one;
  the uninstaller offers to delete it.
- Start menu shortcut, optional desktop and sign-in shortcuts, an entry
  in Settings > Apps, the `.ktl` file association, and an App Paths
  entry so Win+R `kestrel` works.
- Updates are unpacked next to the installed version and swapped in with
  two renames, so a failed or canceled update leaves the old one exactly
  as it was. Running the same version again offers a reinstall.
- A running Kestrel is asked to close first. An install made by the old
  Inno Setup installer is uninstalled first, quietly.
- `Uninstall Kestrel.exe` removes the install folder, itself included:
  it moves itself to `%TEMP%` before deleting the folder, and that copy
  goes at the next restart.
- One setup window at a time: starting setup again brings the open one
  to the front.
- Everything it does is logged to `%TEMP%\Kestrel Setup.log`.

## Command line

| Argument | |
|---|---|
| `/S` | Silent: no window, defaults unless given below |
| `/D=<folder>` | Install folder (last argument, may contain spaces) |
| `--desktop-shortcut=0\|1`, `--startup=0\|1` | Shortcut choices |
| `--launch` | Start Kestrel when done |
| `--force-close` | End a Kestrel that does not close when asked |

The uninstaller takes `/S`, and `/S --delete-data` to delete user data
too. A silent run exits with 0 on success, 1 on failure, and 3 when
Kestrel was running and did not close (without `--force-close`).

## Testing

- `python installer/setup/tests/test_payload.py` packs a tree, unpacks
  it with the installer's own C extractor compiled for Linux, and
  compares byte for byte. Damaged and hostile payloads must be refused.
- `make -C installer/setup/preview` builds a Linux-only preview that
  renders the setup screens to PNG (needs FreeType and libpng).
- The stub has been run under Wine: install, update, reinstall, cancel,
  silent install and uninstall, and 150% DPI. It has not been run on a
  real Windows machine from here, and it is unsigned, so SmartScreen
  shows "Unknown publisher" until it is signed (see
  `docs/ANTIVIRUS.md`).

## Inno Setup

`installer/Kestrel.iss` still builds the previous installer
(`iscc installer\Kestrel.iss`) if you need it. Its output goes to
`installer/Output/`.
