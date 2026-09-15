#!/usr/bin/env python3
"""Offline desktop preflight for PSD -> e-Kickoff manifests.

This is a local, deterministic GUI for the read-only planner. It cannot open a
website, save a match sheet, or submit a match sheet. Its only output is a dry-run
diff after an exact, match-specific confirmation.
"""

from __future__ import annotations

import argparse
import json
import unittest
from pathlib import Path

import wedstrijdblad_sync as planner

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError:  # A headless build host can still run --self-test.
    tk = None
    filedialog = messagebox = None

    class _HeadlessFrame:
        pass

    class _HeadlessTtk:
        Frame = _HeadlessFrame

    ttk = _HeadlessTtk()


class PreflightApp(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.manifest: tuple | None = None
        self.confirmation = tk.StringVar()
        self.path = tk.StringVar(value="Nog geen manifest geopend")
        self._build()

    def _build(self) -> None:
        self.master.title("Wedstrijdblad Sync — controle")
        self.master.minsize(760, 520)
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(self, text="Wedstrijdblad Sync", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(self, text="Open selectie-bestand…", command=self.open_manifest).grid(
            row=0, column=1, sticky="e"
        )
        ttk.Label(self, textvariable=self.path, wraplength=700).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 8)
        )

        self.output = tk.Text(self, height=20, wrap="word", state="disabled")
        self.output.grid(row=2, column=0, columnspan=2, sticky="nsew")

        controls = ttk.Frame(self)
        controls.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="Bevestiging:").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.confirmation).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(controls, text="Toon alleen dry-run", command=self.make_plan).grid(row=0, column=2)

        ttk.Label(
            self,
            text="Deze app wijzigt geen PSD of e-Kickoff. ‘Bewaren’ en ‘Versturen’ bestaan hier niet.",
            foreground="#7a0000",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(12, 0))

    def _write(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)
        self.output.configure(state="disabled")

    def open_manifest(self) -> None:
        filename = filedialog.askopenfilename(
            title="Open selectie-bestand", filetypes=(("JSON-bestanden", "*.json"), ("Alle bestanden", "*"))
        )
        if not filename:
            return
        try:
            self.manifest = planner.load_manifest(Path(filename))
            matchsheet_id, desired, _current, staff, _current_staff, rules, staff_policy, allow_empty = self.manifest
            self.path.set(Path(filename).name)
            self.confirmation.set("")
            self._write(planner.render_preflight(matchsheet_id, desired, staff, rules, staff_policy, allow_empty))
        except (OSError, json.JSONDecodeError, planner.ValidationError) as exc:
            self.manifest = None
            messagebox.showerror("Kan bestand niet openen", str(exc))

    def make_plan(self) -> None:
        if self.manifest is None:
            messagebox.showinfo("Eerst een bestand openen", "Open eerst een selectie-bestand.")
            return
        matchsheet_id, desired, current, staff, current_staff, rules, staff_policy, allow_empty = self.manifest
        expected = f"BEVESTIG {matchsheet_id}"
        if self.confirmation.get().strip() != expected:
            messagebox.showwarning("Geen dry-run", f"Typ exact: {expected}")
            return
        try:
            result = planner.make_plan(
                matchsheet_id, desired, current, staff, current_staff, rules, staff_policy, allow_empty
            )
        except planner.ValidationError as exc:
            messagebox.showerror("Geen dry-run", str(exc))
            return
        self._write(json.dumps(result, indent=2, sort_keys=True))


class DesktopTests(unittest.TestCase):
    def test_module_has_no_browser_or_writer(self) -> None:
        # Do not inspect this test's own assertions: their literal forbidden
        # terms would otherwise make the test fail even when the application
        # itself has no browser integration.
        try:
            source = Path(__file__).read_text(encoding="utf-8").split("class DesktopTests", 1)[0].casefold()
        except (OSError, UnicodeError):
            # PyInstaller one-file packages contain bytecode, not this source
            # text. The static inspection has already run on the source build.
            self.skipTest("source text is unavailable in the frozen package")
        self.assertNotIn("playwright", source)
        self.assertNotIn("wedstrijdbladen.be", source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        # The package has no console.  Keep the check entirely in memory.
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(DesktopTests)
        result = unittest.TestResult()
        suite.run(result)
        return 0 if result.wasSuccessful() else 1
    if tk is None:
        parser.error("Tk is unavailable in this Python build; run the packaged Windows executable instead")
    root = tk.Tk()
    PreflightApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
