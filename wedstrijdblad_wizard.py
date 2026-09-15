#!/usr/bin/env python3
"""Windows-friendly, read-only PSD selection wizard.

The wizard is the human-facing front end for ``psd_reader``.  It opens PSD in
an isolated visible browser profile, shows the selected players only in this
window, asks for the two facts PSD's selection table cannot prove (captain and
goalkeeper), and writes a local pseudonymous JSON manifest after an exact,
match-specific confirmation.  It cannot save or send anything to PSD or
e-Kickoff.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import unittest
from pathlib import Path

import psd_reader

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ModuleNotFoundError:  # Keep --self-test usable on a headless build host.
    tk = None
    filedialog = messagebox = None

    class _HeadlessFrame:
        pass

    class _HeadlessTtk:
        Frame = _HeadlessFrame

    ttk = _HeadlessTtk()


class SelectionWizard(ttk.Frame):
    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.manifest: dict | None = None
        self.choices: list[tuple[str, psd_reader.RawPlayerRow]] = []
        self.matchsheet_id = tk.StringVar()
        self.date = tk.StringVar()
        self.opponent = tk.StringVar()
        self.starters = tk.StringVar(value="11")
        self.substitutes = tk.StringVar(value="3")
        self.captain = tk.StringVar()
        self.goalkeeper = tk.StringVar()
        self.confirmation = tk.StringVar()
        self.status = tk.StringVar(value="Vul wedstrijdblad-ID, datum en tegenstander in.")
        self._build()

    def _build(self) -> None:
        self.master.title("Wedstrijdblad Sync — PSD selectie lezen")
        self.master.minsize(780, 560)
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(6, weight=1)

        ttk.Label(self, text="PSD-selectie lezen", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(self, text="Wedstrijdblad-ID:").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(self, textvariable=self.matchsheet_id).grid(row=1, column=1, sticky="ew", pady=(12, 0))
        ttk.Label(self, text="Datum (DD/MM/JJJJ):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(self, textvariable=self.date).grid(row=2, column=1, sticky="ew", pady=(6, 0))
        ttk.Label(self, text="Tegenstander:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(self, textvariable=self.opponent).grid(row=3, column=1, sticky="ew", pady=(6, 0))
        counts = ttk.Frame(self)
        counts.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(counts, text="Basis:").grid(row=0, column=0, sticky="w")
        ttk.Entry(counts, width=5, textvariable=self.starters).grid(row=0, column=1, padx=(4, 12))
        ttk.Label(counts, text="Wissels:").grid(row=0, column=2, sticky="w")
        ttk.Entry(counts, width=5, textvariable=self.substitutes).grid(row=0, column=3, padx=(4, 0))
        self.read_button = ttk.Button(self, text="Lees PSD-selectie", command=self.read_psd)
        self.read_button.grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 10))

        self.tree = ttk.Treeview(self, columns=("number", "name", "role"), show="headings", height=12)
        self.tree.heading("number", text="Nr.")
        self.tree.heading("name", text="Speler (alleen tijdelijk)")
        self.tree.heading("role", text="PSD-status")
        self.tree.column("number", width=60, stretch=False)
        self.tree.column("name", width=420)
        self.tree.column("role", width=120, stretch=False)
        self.tree.grid(row=6, column=0, columnspan=3, sticky="nsew")

        ttk.Label(self, text="Kapitein (basis):").grid(row=7, column=0, sticky="w", pady=(10, 0))
        self.captain_box = ttk.Combobox(self, textvariable=self.captain, state="disabled")
        self.captain_box.grid(row=7, column=1, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(self, text="Doelman:").grid(row=8, column=0, sticky="w", pady=(6, 0))
        self.goalkeeper_box = ttk.Combobox(self, textvariable=self.goalkeeper, state="disabled")
        self.goalkeeper_box.grid(row=8, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(self, text="Typ exact BEVESTIG <wedstrijdblad-ID>:").grid(row=9, column=0, sticky="w", pady=(10, 0))
        self.confirmation_entry = ttk.Entry(self, textvariable=self.confirmation, state="disabled")
        self.confirmation_entry.grid(row=9, column=1, columnspan=2, sticky="ew", pady=(10, 0))
        self.write_button = ttk.Button(
            self, text="Maak lokaal controlebestand", command=self.write_manifest, state="disabled"
        )
        self.write_button.grid(row=10, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Label(self, textvariable=self.status, wraplength=730).grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(12, 0)
        )
        ttk.Label(
            self,
            text="Deze wizard leest alleen. PSD/e-Kickoff bewaren en versturen bestaan hier niet.",
            foreground="#7a0000",
        ).grid(row=12, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _fixture(self) -> tuple[psd_reader.Fixture, int, int]:
        fixture = psd_reader.Fixture(self.matchsheet_id.get().strip(), self.date.get().strip(), self.opponent.get().strip())
        fixture.validate()
        try:
            starters, substitutes = int(self.starters.get()), int(self.substitutes.get())
        except ValueError as exc:
            raise psd_reader.SnapshotError("Basis en wissels moeten gehele getallen zijn") from exc
        psd_reader._selection_rules(starters, substitutes)
        return fixture, starters, substitutes

    def read_psd(self) -> None:
        try:
            fixture, starters, substitutes = self._fixture()
        except (psd_reader.ReadOnlyViolation, psd_reader.SnapshotError) as exc:
            messagebox.showerror("Kan PSD niet lezen", str(exc))
            return
        self.read_button.configure(state="disabled")
        self.status.set(
            "PSD wordt zichtbaar geopend. Meld aan en kies, indien gevraagd, het profiel met toegang tot de selecties; "
            "de wizard leest alleen."
        )
        threading.Thread(target=self._read_worker, args=(fixture, starters, substitutes), daemon=True).start()

    def _read_worker(self, fixture: psd_reader.Fixture, starters: int, substitutes: int) -> None:
        try:
            snapshot = asyncio.run(
                psd_reader.read_psd_snapshot(
                    fixture, psd_reader.default_profile_dir(), psd_reader.default_browser_channel(), starters=starters, substitutes=substitutes,
                    prompt_for_login=False,
                )
            )
        except Exception as exc:  # Surface a safe message in the main Tk event loop.
            self.after(0, lambda detail=str(exc): self._read_failed(detail))
            return
        self.after(0, lambda: self._read_finished(*snapshot))

    def _read_failed(self, detail: str) -> None:
        self.read_button.configure(state="normal")
        self.status.set("PSD-selectie is niet gelezen.")
        messagebox.showerror("Kan PSD niet lezen", detail)

    def _read_finished(self, manifest: dict, rows: list[psd_reader.RawPlayerRow]) -> None:
        self.read_button.configure(state="normal")
        self.manifest = manifest
        self.choices = []
        desired_ids = {player["psd_member_id"] for player in manifest["desired"]}
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in rows:
            if row.starter_active == row.substitute_active:
                continue
            member_id = psd_reader.opaque_member_id(row.display_name)
            if member_id not in desired_ids:
                continue
            self.choices.append((member_id, row))
            role = "basis" if row.starter_active else "wissel"
            self.tree.insert("", "end", values=(row.shirt_number, row.display_name, role))
        labels = [self._label(index, row) for index, (_member_id, row) in enumerate(self.choices, start=1)]
        captain_labels = [
            self._label(index, row)
            for index, (_member_id, row) in enumerate(self.choices, start=1)
            if row.starter_active
        ]
        self.captain.set("")
        self.goalkeeper.set("")
        self.confirmation.set("")
        self.captain_box.configure(values=captain_labels, state="readonly")
        self.goalkeeper_box.configure(values=labels, state="readonly")
        self.confirmation_entry.configure(state="normal")
        self.write_button.configure(state="normal" if labels and captain_labels else "disabled")
        rules = manifest["selection_rules"]
        self.status.set(
            f"{len(labels)} geselecteerde spelers gelezen; verwacht {rules['starters']} basis + "
            f"{rules['substitutes']} wissels. Kies kapitein en doelman, daarna bevestigen."
        )

    @staticmethod
    def _label(index: int, row: psd_reader.RawPlayerRow) -> str:
        return f"{index}. #{row.shirt_number} {row.display_name}"

    def _member_from_label(self, label: str) -> str:
        try:
            index = int(label.split(".", 1)[0])
            return self.choices[index - 1][0]
        except (IndexError, ValueError):
            raise psd_reader.SnapshotError("kies een speler uit de gelezen PSD-selectie") from None

    def write_manifest(self) -> None:
        if self.manifest is None:
            return
        expected = f"BEVESTIG {self.manifest['matchsheet_id']}"
        if self.confirmation.get().strip() != expected:
            messagebox.showwarning("Geen bestand", f"Typ exact: {expected}")
            return
        try:
            resolved = psd_reader.apply_human_roles(
                self.manifest, self._member_from_label(self.captain.get()), self._member_from_label(self.goalkeeper.get())
            )
        except psd_reader.SnapshotError as exc:
            messagebox.showwarning("Geen bestand", str(exc))
            return
        filename = filedialog.asksaveasfilename(
            title="Bewaar lokaal controlebestand",
            defaultextension=".json",
            filetypes=(("JSON-bestanden", "*.json"),),
            initialfile="psd-selectie.json",
        )
        if not filename:
            return
        try:
            Path(filename).write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Kan bestand niet schrijven", str(exc))
            return
        self.status.set(f"Lokaal controlebestand gemaakt: {Path(filename).name}. Er is niets naar PSD/e-Kickoff gestuurd.")
        messagebox.showinfo("Klaar", "Het lokale controlebestand is gemaakt. Open het nu in Wedstrijdblad Controle.")


class WizardTests(unittest.TestCase):
    def test_protected_fixture_remains_rejected(self) -> None:
        with self.assertRaises(psd_reader.ReadOnlyViolation):
            psd_reader.Fixture("4344375", "12/09/2026", "KAC Betekom").validate()

    def test_role_choice_uses_row_position_not_player_name(self) -> None:
        self.assertEqual(SelectionWizard._label(2, psd_reader.RawPlayerRow("4", "Voorbeeld Speler", True, False)), "2. #4 Voorbeeld Speler")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        # A PyInstaller --windowed executable has no reliable stdout/stderr.
        # Run silently so this verification path is usable in the package too.
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(WizardTests)
        result = unittest.TestResult()
        suite.run(result)
        return 0 if result.wasSuccessful() else 1
    if tk is None:
        parser.error("Tk is unavailable in this Python build; run the packaged Windows executable instead")
    root = tk.Tk()
    SelectionWizard(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
