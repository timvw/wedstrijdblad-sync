# Windows release checklist

Build the distributable on a clean Windows x64 machine. A macOS build cannot create a testable
Windows executable. Windows 11 on ARM is useful for acceptance testing because it emulates x64
executables, but a native ARM64 Python runtime produces an ARM64 build rather than the x64
release. Use the installed Microsoft Edge channel; do not package or download a second browser.

1. Run `build-windows-reader.bat` from this directory with the regular Python.org 3.11+ runtime
   (including Tcl/Tk) and Microsoft Edge installed. The build fails early when Tk is unavailable;
   do not distribute GUI executables from an embeddable/minimal Python installation.
   On Windows ARM, set `WEDSTRIJDBLAD_PYTHON` to an installed x64 `python.exe` before invoking the
   batch file, so PyInstaller builds the distributable x64 architecture rather than ARM64.
2. Confirm that `dist` contains `wedstrijdblad-psd-wizard.exe`,
   `wedstrijdblad-dryrun-wizard.exe`, `wedstrijdblad-psd-lezer.exe`,
   `wedstrijdblad-reader.exe`, `wedstrijdblad-adf-probe.exe`, `wedstrijdblad-dryrun.exe` and
   `wedstrijdblad-controle.exe`.
3. Run every executable's `--self-test` where applicable.  Do this before signing or distributing.
4. On a separate Windows user account, start the PSD wizard.  Verify it opens its own visible Edge
   profile and that manual PSD login works.  Do not enter or log a password in a terminal.
5. Use only a designated non-official e-Kickoff test sheet for the **Dry-run Wizard**.
   Confirm it supports a deliberately chosen historical PSD source, waits for visible PSD and RBFA
   login, asks explicitly for captain, goalkeeper and `BEVESTIG <doel-ID>`, then creates no
   selection before the dry-run proposal. Confirm the separate plan-specific confirmation is
   required before any concept action. On that designated sheet, verify one controlled unsaved
   concept add/remove cycle, with a reread after every action. Also verify that a
   pre-populated players and staff require a visible explicit one-to-one mapping before it offers
   any action. Verify one controlled combined unsaved cycle (player add/number/GK/captain plus
   existing-staff function update), then restore the original test state and reread it. A new
   staff row must stop safely unless its autocomplete result is exactly unique. Verify that no
   official submission control is invoked or offered by the executable.
6. Let the RBFA session expire once.  Confirm that the reader reports an expired login instead of
   reporting that no match exists.
7. On a permitted empty test sheet, run `wedstrijdblad-adf-probe.exe <id>` and inspect its JSON:
   it may contain only the whitelisted control labels, opaque ADF IDs and fixed table headers—never
   player/staff names, birth dates, values, cookies or request payloads.
8. Run an antivirus/signing review.  Sign the executables with the distributor's Windows code-
   signing certificate before end-user release.
9. Distribute [START-HERE-WINDOWS.md](START-HERE-WINDOWS.md) beside the signed executables. Verify
   that the guide correctly states that both saving and official submission remain manual.

Release only the executables and concise user instructions.  Do not distribute browser profiles,
cookies, credentials, manifests containing live player data, or diagnostic logs.
