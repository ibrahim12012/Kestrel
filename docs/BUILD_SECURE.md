# Build hardening: code signing

Code signing needs a certificate only you can obtain (an EV certificate
is issued to a verified legal entity; an Apple Developer ID needs your
own Apple Developer Program enrollment). This doc gets the build
pipeline ready for it so that, once you have one, wiring it in is a
config change, not new code.

For the antivirus false-positive problem specifically, including what
was already fixed in `Kestrel.spec` and what signing does and does not
solve, see **`docs/ANTIVIRUS.md`** - that is the fuller treatment and
this file covers only the signing half of it.

## Obfuscation: don't

This section used to recommend PyArmor or Cython, to stop anyone
extracting the bundle and decompiling it back to near-original source.
That advice is withdrawn. It is wrong here for two reasons.

It makes the antivirus problem worse. Obfuscated bytecode inside an
executable is a detection heuristic in its own right, for exactly the
same reason UPX packing is (see `docs/ANTIVIRUS.md`): concealing what
code does is what code that does something bad has to do, so engines
score it. Kestrel already sits close enough to the line on behaviour
alone. Adding a layer whose entire purpose is to make the binary harder
to inspect is the wrong direction.

It is also moot. Kestrel is GPL v3 (see `LICENSE`), so the source is
published. There is nothing for a decompiler to reveal that is not
already in the repository, and the licence requires that it stay that
way for anyone distributing a derivative. Obfuscating a build of code
you publish anyway buys build fragility and detections in exchange for
nothing.

If a commercial licensee has a genuine reason to ship an obfuscated
build (the commercial licence does not carry the GPL's source
obligation - see `COMMERCIAL-LICENSE.md`), that is their build to make,
and they should expect to do the antivirus submission work in
`docs/ANTIVIRUS.md` section 4 for every release.

## Code signing

**This step needs your own certificate - nothing here substitutes for
that.**

### Windows: EV Code Signing Certificate

1. Purchase an EV cert from a CA (DigiCert, Sectigo, SSL.com, ...) - this requires real business verification and typically ships on a
   hardware token (USB or cloud HSM), which is what EV actually buys
   you: immediate SmartScreen reputation instead of the "Unknown
   Publisher" warning an unsigned or standard-cert `.exe` gets.
2. Sign the built `.exe` before running Inno Setup, and again sign the
   installer output:
   ```bat
   signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 dist\Kestrel\Kestrel.exe
   iscc installer\Kestrel.iss
   signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 installer\Output\KestrelSetup.exe
   ```
3. `installer/Kestrel.iss` has a commented `SignTool=` line showing
   where it goes once you have a cert - see that file.
4. `docs/ANTIVIRUS.md` section 4 covers the OV-vs-EV choice (only EV
   clears SmartScreen immediately), submitting false positives to
   Microsoft in the meantime, and rebuilding the PyInstaller
   bootloader, which is the most effective free step.

### macOS: Apple Developer ID + notarization

1. Enroll in the Apple Developer Program (apple.com/developer),
   which issues a "Developer ID Application" certificate.
2. Sign and notarize:
   ```bash
   codesign --deep --force --options runtime --sign "Developer ID Application: Your Name (TEAMID)" dist/Kestrel.app
   xcrun notarytool submit dist/Kestrel.zip --apple-id you@example.com --team-id TEAMID --password <app-specific-password> --wait
   xcrun stapler staple dist/Kestrel.app
   ```

## Auto-updates

Deliberately **not** wired to self-install (see `ai/updater.py`'s own
docstring) until code signing above is in place - an auto-updater for
an unsigned binary defeats the trust model that both SmartScreen and
Gatekeeper are built on. Right now it only checks GitHub Releases and
tells the user where to download the new version. Once signing is set
up, the natural next step is a real updater (PyUpdater, or a custom
checker that verifies the new binary's signature before replacing the
running one) - don't skip the signature-verification step even then;
an updater that fetches and runs unsigned code is a bigger attack
surface than not having auto-update at all.
