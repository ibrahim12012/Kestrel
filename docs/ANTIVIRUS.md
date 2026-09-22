# Why antivirus flags Kestrel, and what to do about it

Windows Defender, SmartScreen and most third-party engines will flag a
build of this app. They are not finding malware. They are finding an
unsigned executable that installs global keyboard hooks, injects
synthetic input, captures the screen, runs shell commands and sends
screenshots to a remote server, which is a description of Kestrel and
also a description of a remote access trojan. Nothing in the code
distinguishes those two from the outside.

This document splits the problem into three parts: what has been fixed
in the build, what is inherent to the product, and what actually ends
the detections.

---

## 1. There is no malware in the code

Worth establishing first, because it determines what kind of problem
this is. A full sweep of the codebase found none of the things engines
are actually looking for:

| Behaviour engines score | Present in Kestrel |
|---|---|
| Persistence (registry Run keys, scheduled tasks, services, WMI) | No. Nothing. The only startup option is an opt-in, off-by-default Startup-folder shortcut created by the installer. |
| Process injection (`VirtualAlloc`, `WriteProcessMemory`, `CreateRemoteThread`, `SetWindowsHookEx` with a DLL) | No. The entire codebase contains three `ctypes` calls: taskbar icon grouping, DPI scale factor, and one that has now been removed. |
| Download and execute | No. The updater checks a version number and tells you to download it yourself; it deliberately cannot install anything. |
| Obfuscated or encoded payloads (`marshal`, `pickle`, encoded blobs, packed bytecode) | No. `pickle` and `marshal` do not appear in the project at all. |
| Credential theft (browser profiles, cookie databases, password stores, SSH keys, wallets) | No. The only `%APPDATA%` path the app computes is its own. |
| Anti-analysis (VM detection, debugger detection, timing checks) | No. |
| Clipboard reading | No. The clipboard is written to (to type characters `pyautogui` mishandles) and never read. |
| Bundled binaries in the repo | None. No `.exe`, `.dll`, `.sys` or `.msi` anywhere. |
| `shell=True` | Nowhere. Every subprocess call uses an argument list. |

So this is a false positive problem, not a "clean up the malware"
problem. That matters, because the fixes for the two are completely
different.

---

## 2. What was fixed in the build

These were real contributors and are now gone.

### UPX packing (the big one)

`Kestrel.spec` had `upx=True` on both `EXE` and `COLLECT`. Both are now
`upx=False`.

Executable packing is one of the highest-weighted static heuristics
there is. The reason is simple: packing rewrites the whole binary into a
compressed blob with a small unpacking stub, which defeats signature
matching, which is why malware does it. Legitimate software almost
entirely stopped packing years ago precisely because of the detections.
Several engines flag UPX-packed PyInstaller output on the packing alone,
with no other signal. It was saving perhaps 30 MB.

### No version resource

The executable carried no VERSIONINFO resource at all: no publisher, no
product name, no description, no version. An unsigned binary with blank
file properties scores on that by itself, because effectively nothing
built by a real publisher ships without one.

`Kestrel.spec` now generates one from `branding.PUBLISHER` /
`PRODUCT_DESCRIPTION` / `COPYRIGHT` and `paths.APP_VERSION`, so it
cannot drift from the version the app reports. The installer got the
same treatment via Inno Setup's `VersionInfo*` directives.

### No application manifest

`installer/Kestrel.manifest` is now compiled into the binary. It states
three things explicitly rather than leaving them to a default:

- `requestedExecutionLevel="asInvoker"` - the app never asks for
  administrator. An automation tool that requests elevation is a
  materially different risk profile and gets scored as one.
- `uiAccess="false"` - it does not use the flag that lets a process
  drive input into higher-integrity windows.
- `supportedOS` for Windows 8.1/10/11, plus per-monitor DPI awareness,
  which also gets the DPI numbers reported correctly.

### Writing into its own install directory

Two things wrote files into the program folder at runtime:

- `ai/instructions.txt` - the assistant's system prompt, which the
  `[[UPDATE_START]]` protocol appends learned preferences to. That is an
  application rewriting its own program files with model-authored text.
- `ai/generated/<app>_actions.py` - generated Python modules from a UI
  mapping run. An app writing `.py` files into its install folder is
  describing a dropper.

Both now live under `%LOCALAPPDATA%\Kestrel` with everything else that
is written at runtime. This was a bug independently of antivirus:
Program Files is not writable by a standard user, and an upgrade
replaces the install directory, which would have wiped every learned
preference. Existing installs are migrated on first launch.

### Screen-capture evasion

`app/mapping_monitor.py` called
`SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)` on its status
overlay, so the overlay would not appear in screenshots the app itself
took. **Removed.**

That is the API malware uses to keep a phishing overlay out of a screen
recording, and cheats use to hide from anti-cheat screenshots. It is
specifically watched for. What it bought here was cosmetic: a 300px
status panel in the corner of a screenshot is noise the model ignores,
not a wrong answer. An app that injects input and captures the screen
for a living cannot also afford to look like it is hiding from the
screen.

### A note on obfuscation

`docs/BUILD_SECURE.md` used to recommend PyArmor or Cython to stop the
bundle being decompiled. **Do not do this while you have a detection
problem.** Obfuscated bytecode inside an executable is itself a
heuristic, for the same reason packing is: hiding what the code does is
what code that does something bad needs to do. It would trade a
detection problem for a worse detection problem.

It is also moot. Kestrel is GPL v3 (see `LICENSE`), so the source is
published anyway; there is nothing to hide from a decompiler.

---

## 3. What is inherent to the product

The following cannot be removed without removing the app. Listing them
so the trade-off is explicit rather than discovered later:

| Behaviour | Where | Why it is scored |
|---|---|---|
| Global low-level keyboard hook | `app/workers/recorder_worker.py`, four app hotkeys in `app/main_window.py` | Indistinguishable from a keylogger at the API level |
| Global mouse hook, including movement | `app/workers/recorder_worker.py` | Same |
| Synthetic input injection | `pyautogui` throughout `ai/actions.py` | Input injection is how a RAT drives a machine |
| Full-screen capture | `ai/main.py`, `ai/actions.py` | Screen capture plus network egress is the exfiltration shape |
| Screenshots sent to a remote API | AI providers, every vision turn and every autonomous step | See above; the destination being Google or Anthropic is not something a scanner can tell from the binary |
| Shell command execution | `tool_run_command` | Remote command execution |
| Process enumeration and termination | `tasklist`, `taskkill` | Malware disables security software this way |
| UI Automation tree walking | `ai/discovery/` | Reading other applications' contents |
| Continuous microphone listening | wake-word mode, opt-in | Audio capture with network egress |
| `exec()` of generated code | `ai/macro_store.py`, `ai/discovery/compiler.py` | Dynamic code execution |

Two of these are worth defending on their own terms if you ever have to
explain the app to a reviewer, because the code is genuinely careful and
the carefulness is checkable:

- **`tool_run_command`** does not pass anything to a shell. It rejects
  shell metacharacters, tokenizes with `shlex`, checks the base command
  against an allow-list of 34, blocks a list including `format`,
  `diskpart`, `bcdedit`, `vssadmin` and `powershell`, requires a
  blocking human approval dialog for high-risk commands, and rate-limits
  per risk tier. See `ai/safety.py`.
- **The two `exec()` sites** never execute model free-text. They render
  Python from a schema-validated JSON spec with every interpolated value
  passed through `repr()`, drop unknown step types rather than emitting
  them, and `ast.parse()` the result before running it. There are
  dedicated injection tests in `tests/test_macro_generalizer.py`.

If you ever decide to cut scope to reduce the surface, the honest
ranking of what costs least to lose is: wake-word listening first
(opt-in, rarely used, and the one continuously-transmitting path), then
`tool_run_command` (the highest-consequence tool, and the AI can do most
of what it is used for through the UI), then autonomous mode's
per-step screenshot loop. Recording, playback and hotkeys are the
product and should not be touched.

---

## 4. What actually ends the detections

Everything in section 2 reduces the score. None of it gets you to zero,
and SmartScreen's "Windows protected your PC - Unknown publisher" prompt
is not affected by any of it. Three things fix that, in order of
effect:

### Code signing (the real fix)

An Authenticode signature is what turns "Unknown publisher" into your
name. Two grades:

- **OV (organization validation)**, roughly $200-400/year. Signs the
  binary, but SmartScreen reputation still has to accumulate from
  scratch - downloads over time, across machines. Expect weeks of
  warnings before it clears, and the clock resets somewhat on each new
  certificate.
- **EV (extended validation)**, roughly $400-700/year, ships on a
  hardware token or cloud HSM. Grants SmartScreen reputation
  **immediately**. This is the only thing that removes the warning on
  day one.

Both require a verified legal entity - a registered company, not an
individual. If you are shipping as a person rather than a company, this
is the wall you hit, and forming a company is genuinely part of the
answer.

Sign the exe first, then build the installer, then sign the installer:

```bat
signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 dist\Kestrel\Kestrel.exe
iscc installer\Kestrel.iss
signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 installer\Output\KestrelSetup.exe
```

`installer/Kestrel.iss` has a commented `SignTool=` line for step two.
Set `branding.PUBLISHER` to the exact legal entity on the certificate - a mismatch between the signature subject and the version resource is
worse than leaving it generic.

### Submit the false positive to Microsoft

Free, and it works. Submit the build at
<https://www.microsoft.com/en-us/wdsi/filesubmission> as a software
developer, describing what the app does. Turnaround is typically 1-3
days, after which Defender stops flagging that build.

The catch is that it is per-build: every new release needs a new
submission until the signing certificate has reputation. Worth
scripting into your release checklist. For third-party engines, submit
to each vendor separately, or use VirusTotal to see which engines are
actually firing before chasing all of them.

### Rebuild the PyInstaller bootloader

Several engines carry generic signatures keyed to the bytes of the
**prebuilt** PyInstaller bootloader that ships on PyPI, because so much
commodity malware is lazily packaged with it. Compiling your own
bootloader produces different bytes and sidesteps those signatures
entirely. It needs a C compiler (Visual Studio Build Tools):

```bat
git clone https://github.com/pyinstaller/pyinstaller.git
cd pyinstaller\bootloader
python ./waf all
cd ..
pip install .
```

Then rebuild with `pyinstaller Kestrel.spec` as usual. This is the
single most effective free step after not packing with UPX.

---

## 5. The zip is not a container, and not a fix

If the `.zip` is what gets flagged, nothing new is happening. Defender
scans **inside** archives: when the zip is written, when it is read,
when it is extracted, and during any full scan. A detection on the zip
is a detection on a file inside it, almost always `Kestrel.exe`.

Before anything else, find out which build is in there. Run:

```bash
python tools/verify_build.py
```

against the `dist/Kestrel/` folder the zip was made from. If it reports
UPX packing or a missing version resource, that build predates the spec
fixes and a detection on it says nothing about the current one - rebuild with `pyinstaller --clean Kestrel.spec` and re-test. If it
passes every check and the binary is still flagged, that is the
expected state for an unsigned tool that hooks the keyboard and
captures the screen, and section 4 is the answer.

### Get the detection name

This is the single most useful piece of information and it takes ten
seconds. In PowerShell:

```powershell
Get-MpThreat | Select-Object -Last 5 ThreatName, Resources
```

or to scan one file on demand and see what comes back:

```powershell
& "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" -Scan -ScanType 3 -File "C:\path\to\Kestrel.zip"
```

What the name tells you:

- Anything ending in **`!ml`** - `Trojan:Win32/Wacatac.B!ml`,
  `Program:Win32/Wacapew.C!ml`, `Trojan:Win32/Sabsik.FL.A!ml` - is a
  machine-learning verdict, not a signature match. These are the
  classic PyInstaller false positives, they are exactly what the
  Microsoft submission process in section 4 is for, and they are
  usually cleared within a few days.
- A name **without** `!ml` is a signature match on specific bytes. If
  you see one, note which file in `Resources` it points at. If it is
  not `Kestrel.exe`, a dependency shipped something unexpected and that
  is worth chasing on its own.
- `Behavior:Win32/...` is a runtime behavioural detection rather than
  a file scan, which means it fired on what the app did, not on what it
  is. Those do not go away with signing, and the submission needs to
  describe the behaviour.

### Stop shipping a zip

A zip is the worst distribution shape available here, for three
reasons, and the third is the one that matters:

1. **It hides nothing.** See above.
2. **Mark of the Web propagates.** A zip downloaded from the internet
   carries a zone marker, and Windows Explorer (and 7-Zip since 22.00)
   copies that marker onto every file extracted from it. The extracted
   `Kestrel.exe` gets the SmartScreen prompt regardless of the zip.
3. **A zip can never build reputation.** SmartScreen reputation
   attaches to a signing certificate, or, for unsigned files, to an
   exact file hash. A zip has no publisher, and every rebuild is a
   brand-new hash starting from zero. You could ship fifty releases and
   the fifty-first would be just as unknown as the first. A signed
   installer is one artifact whose reputation accumulates against your
   certificate across releases - that is the entire mechanism, and a
   zip opts out of it.

Ship `installer/Output/KestrelSetup.exe`. It is one file, it can be
signed, it accumulates reputation, and it puts things where they belong
instead of asking people to extract a folder somewhere and hope.

### Do not password-protect the archive

It comes up, so: encrypting the zip does stop the scanner reading
inside it, and it is the wrong move. Engines score encrypted archives
*higher*, not lower, precisely because that is what is done to smuggle
payloads past a scanner. It also trains the people who trust you to
type a password to open software that their antivirus objected to,
which is a habit worth more to an attacker than anything in this
repository. The same goes for renaming the extension, splitting the
archive, or anything else whose purpose is to make the file harder to
inspect.

## 6. Release checklist

1. `pyinstaller --clean Kestrel.spec`.
2. `python tools/verify_build.py` - must report no packing and a
   populated version resource before going any further. Everything
   below is wasted effort on a build that fails this.
3. Sign `dist\Kestrel\Kestrel.exe`, if you have a certificate.
4. `iscc installer\Kestrel.iss`.
5. Sign `installer\Output\KestrelSetup.exe`.
6. Upload the installer to VirusTotal. Note which engines fire, and
   whether the names carry `!ml` (see section 5).
7. Submit to Microsoft, and to any other engine that fired.
8. Publish the SHA-256 that `verify_build.py` printed next to the
   download, so anyone who does get a warning can verify they have the
   file you built.
9. Ship the installer. Not a zip - section 5.

## 7. What to tell users in the meantime

Do not pretend the warning is not there - an automation tool asking
people to ignore a security prompt is a bad habit to teach. Say what it
is. Something like:

> Kestrel controls your keyboard, mouse and screen, which is what
> antivirus software is built to be suspicious of, and the build is not
> yet code-signed. You will likely see a SmartScreen warning on first
> run. The source is public (GPL v3) and the SHA-256 of this release is
> below, so you can verify you have the file we built. If you would
> rather not take our word for it, build it yourself from source - the
> instructions are in the README.

That is a better position than most apps with this behaviour profile
can offer, and it is only available because the source is open.
