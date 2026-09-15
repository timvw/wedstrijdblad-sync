#!/usr/bin/env python3
"""One-window PSD -> e-Kickoff draft synchronisation for novice Windows users.

The program has exactly three visible stages: read a deliberately identified
PSD source, let the user resolve captain/goalkeeper, then read the selected
e-Kickoff match sheet and show a deterministic proposal. A second exact
confirmation can apply that proposal as an *unsaved* draft. It contains no
official submission action.

Player names are presentation-only in the live window.  The resulting plan is
kept in memory and shows opaque identifiers only, so closing this app leaves
no new file with player names, browser credentials or cookies.
"""

from __future__ import annotations

import argparse
import asyncio
import threading
import unittest
from typing import Any

import psd_reader
import wedstrijdblad_browser
import wedstrijdblad_combined_sync as combined
import wedstrijdblad_draft_apply as draft_apply
import wedstrijdblad_dryrun as dryrun
import wedstrijdblad_execution_guard as execution_guard
import wedstrijdblad_populated_sync as populated
import wedstrijdblad_staff_sync as staff_sync
import support_report

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ModuleNotFoundError:  # Keep --self-test usable on a headless build host.
    tk = None
    messagebox = None

    class _HeadlessFrame:
        pass

    class _HeadlessTtk:
        Frame = _HeadlessFrame

    ttk = _HeadlessTtk()


def prepare_populated_review(
    manifest: dict[str, Any], target: dict[str, Any], source_rows: list[psd_reader.RawPlayerRow],
    current_rows: tuple[Any, ...], choices: tuple[populated.MappingChoice, ...],
) -> tuple[populated.PopulatedSync, dict[str, Any], execution_guard.ReviewGate, dict[str, Any]]:
    """Build the PII-free, confirmation-bound review after explicit mapping.

    Keeping this outside the Tk class makes the crucial populated-sheet path
    testable without starting a window or a browser.  Names remain only in the
    input-only ``source_rows`` / ``current_rows`` objects.
    """
    sync = populated.build_populated_sync(manifest, source_rows, current_rows, choices)
    plan = populated.make_populated_plan(str(manifest["matchsheet_id"]), sync)
    reviewed_target = {**target, "player_state": draft_apply.opaque_player_state(sync.current)}
    return sync, plan, execution_guard.create_review_gate(plan, reviewed_target), reviewed_target


def prepare_combined_review(
    matchsheet_id: str,
    target: dict[str, Any],
    desired_players: tuple[Any, ...],
    current_players: tuple[Any, ...],
    staff_plan: staff_sync.StaffSync,
) -> tuple[dict[str, Any], execution_guard.ReviewGate, dict[str, Any]]:
    """Build the single player+staff review artefact without any Tk state."""
    reviewed_target = {
        **target,
        "player_state": draft_apply.opaque_player_state(current_players),
        "staff_state": combined.opaque_staff_state(staff_plan.current),
    }
    plan = combined.make_combined_plan(matchsheet_id, desired_players, current_players, staff_plan)
    return plan, execution_guard.create_review_gate(plan, reviewed_target), reviewed_target


class DryRunWizard(ttk.Frame):
    """GUI coordinator.  Browser work runs off the Tk event loop."""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.raw_manifest: dict[str, Any] | None = None
        self.raw_staff: list[psd_reader.RawStaffRow] = []
        self.choices: list[tuple[str, psd_reader.RawPlayerRow]] = []
        self.matchsheet_id = tk.StringVar()
        self.date = tk.StringVar()
        self.opponent = tk.StringVar()
        self.starters = tk.StringVar(value="11")
        self.substitutes = tk.StringVar(value="3")
        self.captain = tk.StringVar()
        self.goalkeeper = tk.StringVar()
        self.confirmation = tk.StringVar()
        self.apply_confirmation = tk.StringVar()
        # Per-run and off by default: opting in never sends an issue; it only
        # opens a prefilled form after a failure for the user to review.
        self.support_opt_in = tk.BooleanVar(value=False)
        self.final_manifest: dict[str, Any] | None = None
        self.target_snapshot: dict[str, Any] | None = None
        self.review_plan: dict[str, Any] | None = None
        self.review_gate: execution_guard.ReviewGate | None = None
        self.populated_sync: populated.PopulatedSync | None = None
        self.staff_sync: staff_sync.StaffSync | None = None
        self.status = tk.StringVar(value="Stap 1: vul doel-wedstrijdblad en PSD-bron in.")
        self._build()

    def _build(self) -> None:
        self.master.title("Wedstrijdblad Sync — veilige dry-run")
        self.master.minsize(840, 690)
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(7, weight=1)

        ttk.Label(self, text="PSD → e-Kickoff: veilige controle", font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            self,
            text="De PSD-bron mag een vorige wedstrijd zijn. Stap 3 past uitsluitend een onbewaard concept toe; versturen bestaat niet in deze app.",
            wraplength=790,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 10))

        ttk.Label(self, text="Doel-wedstrijdblad-ID:").grid(row=2, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.matchsheet_id).grid(row=2, column=1, columnspan=2, sticky="ew")
        ttk.Label(self, text="PSD-brondatum (DD/MM/JJJJ):").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(self, textvariable=self.date).grid(row=3, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(self, text="PSD-tegenstander:").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(self, textvariable=self.opponent).grid(row=4, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        counts = ttk.Frame(self)
        counts.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(counts, text="Vereiste basis:").grid(row=0, column=0, sticky="w")
        ttk.Entry(counts, width=5, textvariable=self.starters).grid(row=0, column=1, padx=(4, 14))
        ttk.Label(counts, text="Vereiste wissels:").grid(row=0, column=2, sticky="w")
        ttk.Entry(counts, width=5, textvariable=self.substitutes).grid(row=0, column=3, padx=(4, 0))
        self.read_button = ttk.Button(self, text="1. Lees PSD-selectie", command=self.read_psd)
        self.read_button.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 10))

        self.tree = ttk.Treeview(self, columns=("number", "name", "role"), show="headings", height=12)
        self.tree.heading("number", text="Nr.")
        self.tree.heading("name", text="Speler (alleen tijdelijk)")
        self.tree.heading("role", text="PSD-status")
        self.tree.column("number", width=60, stretch=False)
        self.tree.column("name", width=510)
        self.tree.column("role", width=120, stretch=False)
        self.tree.grid(row=7, column=0, columnspan=3, sticky="nsew")

        ttk.Label(self, text="Kapitein (basis):").grid(row=8, column=0, sticky="w", pady=(10, 0))
        self.captain_box = ttk.Combobox(self, textvariable=self.captain, state="disabled")
        self.captain_box.grid(row=8, column=1, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(self, text="Doelman:").grid(row=9, column=0, sticky="w", pady=(6, 0))
        self.goalkeeper_box = ttk.Combobox(self, textvariable=self.goalkeeper, state="disabled")
        self.goalkeeper_box.grid(row=9, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(self, text="Typ exact BEVESTIG <doel-ID>:").grid(row=10, column=0, sticky="w", pady=(8, 0))
        self.confirmation_entry = ttk.Entry(self, textvariable=self.confirmation, state="disabled")
        self.confirmation_entry.grid(row=10, column=1, columnspan=2, sticky="ew", pady=(8, 0))
        self.compare_button = ttk.Button(
            self, text="2. Controleer doelblad en toon voorstel", command=self.read_target, state="disabled"
        )
        self.compare_button.grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 0))
        ttk.Label(self, textvariable=self.status, wraplength=790).grid(
            row=12, column=0, columnspan=3, sticky="w", pady=(12, 0)
        )
        ttk.Label(
            self,
            text="Veiligheid: PSD wordt nooit gewijzigd; bestaande e-Kickoff-spelers worden altijd expliciet gekoppeld. Versturen bestaat niet in deze app.",
            foreground="#7a0000",
        ).grid(row=13, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Separator(self).grid(row=14, column=0, columnspan=3, sticky="ew", pady=(12, 8))
        ttk.Label(self, text="Stap 3 — concept toepassen (nooit versturen)", font=("Segoe UI", 11, "bold")).grid(
            row=15, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(self, text="Typ planbevestiging:").grid(row=16, column=0, sticky="w", pady=(6, 0))
        self.apply_entry = ttk.Entry(self, textvariable=self.apply_confirmation, state="disabled")
        self.apply_entry.grid(row=16, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(self, text="Het concept wordt niet bewaard; controleer en bewaar het zelf in e-Kickoff.", foreground="#7a0000").grid(
            row=17, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        self.apply_button = ttk.Button(
            self, text="3. Zet spelers als concept over", command=self.apply_draft, state="disabled"
        )
        self.apply_button.grid(row=18, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(
            self,
            text="Bij een fout een privacyvrije GitHub-supportmelding openen (zelf nakijken en versturen)",
            variable=self.support_opt_in,
        ).grid(row=19, column=0, columnspan=3, sticky="w", pady=(12, 0))

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
        self.compare_button.configure(state="disabled")
        self.status.set(
            "PSD wordt zichtbaar geopend. Meld daar aan en kies, indien gevraagd, het profiel met toegang tot de selecties; "
            "dit venster wacht veilig."
        )
        threading.Thread(target=self._read_psd_worker, args=(fixture, starters, substitutes), daemon=True).start()

    def _read_psd_worker(self, fixture: psd_reader.Fixture, starters: int, substitutes: int) -> None:
        try:
            manifest, rows = asyncio.run(
                psd_reader.read_psd_snapshot(
                    fixture, psd_reader.default_profile_dir(), psd_reader.default_browser_channel(), starters=starters, substitutes=substitutes,
                    prompt_for_login=False,
                )
            )
            raw_staff = asyncio.run(
                psd_reader.read_psd_staff_snapshot(
                    fixture, psd_reader.default_profile_dir(), psd_reader.default_browser_channel(), prompt_for_login=False,
                )
            )
        except Exception as exc:
            self.after(0, lambda problem=exc: self._read_failed(problem))
            return
        self.after(0, lambda: self._read_psd_finished(manifest, rows, raw_staff))

    def _read_failed(self, problem: BaseException) -> None:
        self.read_button.configure(state="normal")
        self.status.set("PSD-selectie is niet gelezen.")
        messagebox.showerror("Kan PSD niet lezen", str(problem))
        self._offer_support_report("psd-read", problem)

    def _read_psd_finished(
        self, manifest: dict[str, Any], rows: list[psd_reader.RawPlayerRow], raw_staff: list[psd_reader.RawStaffRow],
    ) -> None:
        self.read_button.configure(state="normal")
        self.raw_manifest = manifest
        self.raw_staff = raw_staff
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
            self.tree.insert("", "end", values=(row.shirt_number, row.display_name, "basis" if row.starter_active else "wissel"))
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
        counts_ok = self._selected_count_matches(manifest)
        self.compare_button.configure(state="normal" if labels and captain_labels and counts_ok else "disabled")
        rules = manifest["selection_rules"]
        if not counts_ok:
            self.status.set(
                f"PSD toont niet de gevraagde {rules['starters']} basis + {rules['substitutes']} wissels. Geen voorstel mogelijk."
            )
        else:
            self.status.set(
                f"{len(labels)} spelers gelezen. Kies kapitein en doelman en bevestig het doel-ID voor stap 2."
            )

    @staticmethod
    def _selected_count_matches(manifest: dict[str, Any]) -> bool:
        desired = manifest.get("desired", [])
        rules = manifest.get("selection_rules", {})
        return (
            sum(player.get("role") == "starter" for player in desired) == rules.get("starters")
            and sum(player.get("role") == "substitute" for player in desired) == rules.get("substitutes")
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

    def read_target(self) -> None:
        if self.raw_manifest is None:
            return
        expected = f"BEVESTIG {self.raw_manifest['matchsheet_id']}"
        if self.confirmation.get().strip() != expected:
            messagebox.showwarning("Geen voorstel", f"Typ exact: {expected}")
            return
        try:
            manifest = psd_reader.apply_human_roles(
                self.raw_manifest, self._member_from_label(self.captain.get()), self._member_from_label(self.goalkeeper.get())
            )
        except psd_reader.SnapshotError as exc:
            messagebox.showwarning("Geen voorstel", str(exc))
            return
        if any(row.present for row in self.raw_staff):
            function_choices = staff_sync.StaffFunctionDialog(self.master, self.raw_staff).wait()
            if function_choices is None:
                self.status.set("Stafrollen zijn niet bevestigd; er is niets gewijzigd.")
                return
            try:
                manifest = psd_reader.apply_staff_function_choices(manifest, self.raw_staff, function_choices)
            except psd_reader.SnapshotError as exc:
                messagebox.showwarning("Geen voorstel", str(exc))
                return
        # A new target read invalidates every earlier review token, including
        # one made for the same match sheet.
        self.final_manifest = None
        self.target_snapshot = None
        self.review_plan = None
        self.review_gate = None
        self.populated_sync = None
        self.staff_sync = None
        self.compare_button.configure(state="disabled")
        self.status.set("e-Kickoff wordt zichtbaar geopend. Meld bij RBFA aan indien nodig; alleen lezen.")
        threading.Thread(target=self._read_target_worker, args=(manifest,), daemon=True).start()

    def _read_target_worker(self, manifest: dict[str, Any]) -> None:
        try:
            target = asyncio.run(
                wedstrijdblad_browser.read_matchsheet(
                    str(manifest["matchsheet_id"]), wedstrijdblad_browser.default_profile_dir(),
                    wedstrijdblad_browser.default_browser_channel(),
                    prompt_for_login=False,
                )
            )
            if target["players_empty"]:
                target = {**target, "player_state": []}
                result: dict[str, Any] | None = dryrun.plan_empty_target(manifest, target)
                discovered: tuple[Any, ...] = ()
            else:
                # Existing rows are read only into the temporary mapping
                # dialog. They are never put in a plan, file, or log.
                result = None
                discovered = asyncio.run(
                    draft_apply.discover_current_roster(
                        manifest, [row for _member_id, row in self.choices],
                        wedstrijdblad_browser.default_profile_dir(), wedstrijdblad_browser.default_browser_channel(),
                    )
                )
            discovered_staff: tuple[Any, ...] = ()
            if manifest.get("staff_policy") == "synchronize":
                discovered_staff = asyncio.run(
                    draft_apply.discover_current_staff(
                        str(manifest["matchsheet_id"]), wedstrijdblad_browser.default_profile_dir(),
                        wedstrijdblad_browser.default_browser_channel(),
                    )
                )
        except Exception as exc:
            self.after(0, lambda problem=exc: self._target_failed(problem))
            return
        self.after(0, lambda: self._target_finished(manifest, target, result, discovered, discovered_staff))

    def _target_failed(self, problem: BaseException) -> None:
        self.compare_button.configure(state="normal")
        self.status.set("Geen voorstel gemaakt; er is niets gewijzigd.")
        messagebox.showerror("Kan doelblad niet controleren", str(problem))
        self._offer_support_report("target-read", problem)

    def _target_finished(
        self, manifest: dict[str, Any], target: dict[str, Any], result: dict[str, Any] | None, discovered: tuple[Any, ...],
        discovered_staff: tuple[Any, ...],
    ) -> None:
        if result is None:
            dialog = populated.MappingDialog(
                self.master, [row for _member_id, row in self.choices], manifest["desired"], discovered,
            )
            choices = dialog.wait()
            if choices is None:
                self.compare_button.configure(state="normal")
                self.status.set("Bestaande spelers zijn niet gekoppeld; er is niets gewijzigd.")
                return
            try:
                self.populated_sync, result, self.review_gate, target = prepare_populated_review(
                    manifest, target, [row for _member_id, row in self.choices], discovered, choices,
                )
            except Exception as exc:
                self.compare_button.configure(state="normal")
                self.status.set("Koppelen stopte; er is niets gewijzigd.")
                messagebox.showerror("Geen voorstel", str(exc))
                self._offer_support_report("target-read", exc)
                return
        else:
            self.populated_sync = None

        self.staff_sync = None
        if manifest.get("staff_policy") == "synchronize":
            dialog = staff_sync.StaffMappingDialog(self.master, self.raw_staff, manifest["desired_staff"], discovered_staff)
            choices = dialog.wait()
            if choices is None:
                self.compare_button.configure(state="normal")
                self.status.set("Bestaande staf is niet gekoppeld; er is niets gewijzigd.")
                return
            try:
                self.staff_sync = staff_sync.build_staff_sync(manifest, self.raw_staff, discovered_staff, choices)
                player_desired = self.populated_sync.desired if self.populated_sync else draft_apply._desired_players(manifest)
                player_current = self.populated_sync.current if self.populated_sync else ()
                result, self.review_gate, target = prepare_combined_review(
                    str(manifest["matchsheet_id"]), target, player_desired, player_current, self.staff_sync,
                )
            except Exception as exc:
                self.compare_button.configure(state="normal")
                self.status.set("Staf koppelen stopte; er is niets gewijzigd.")
                messagebox.showerror("Geen voorstel", str(exc))
                self._offer_support_report("target-read", exc)
                return

        self.final_manifest = manifest
        self.target_snapshot = target
        self.review_plan = result
        self.review_gate = self.review_gate or execution_guard.create_review_gate(result, target)
        self.apply_confirmation.set("")
        self.apply_entry.configure(state="normal")
        self.apply_button.configure(state="normal")
        action_counts = self._action_counts(result)
        summary = ", ".join(f"{count}× {kind}" for kind, count in sorted(action_counts.items())) or "geen wijzigingen"
        state = "leeg" if target["players_empty"] else "bestaande spelers expliciet gekoppeld"
        self.status.set(f"Klaar: doelblad was {state}; voorstel: {summary}. Typ voor concepttoepassing exact: {self.review_gate.confirmation}")
        messagebox.showinfo(
            "Voorstel gecontroleerd",
            f"Het voorstel voor {manifest['matchsheet_id']} is gemaakt.\n\n"
            f"Spelers op doelblad: {state}\n"
            f"Acties: {summary}\n"
            f"Staf: {'expliciet gekoppeld' if self.staff_sync else 'behouden'}\n\nEr is niets bewaard of verstuurd.",
        )

    @staticmethod
    def _action_counts(plan: dict[str, Any]) -> dict[str, int]:
        """Summarise either planner plan format without leaking player data."""
        operations = plan.get("operations", {})
        if isinstance(operations, list):
            counts: dict[str, int] = {}
            for item in operations:
                kind = str(item.get("kind", "onbekend"))
                counts[kind] = counts.get(kind, 0) + 1
            return counts
        players = operations.get("players", {}) if isinstance(operations, dict) else {}
        return {
            "add": len(players.get("add", ())),
            "remove": len(players.get("remove", ())),
            "shirt_number": len(players.get("set_numbers", ())),
            "goalkeeper": len(players.get("set_goalkeeper", ())),
            "captain": len(players.get("set_captain", ())),
        }

    def apply_draft(self) -> None:
        if not all((self.final_manifest, self.target_snapshot, self.review_plan, self.review_gate)):
            return
        if self.apply_confirmation.get().strip() != self.review_gate.confirmation:
            messagebox.showwarning("Geen conceptwijziging", f"Typ exact: {self.review_gate.confirmation}")
            return
        self.apply_button.configure(state="disabled")
        self.status.set("e-Kickoff wordt opnieuw gelezen; het concept stopt bij elke afwijking sinds de review.")
        threading.Thread(target=self._apply_worker, daemon=True).start()

    def _apply_worker(self) -> None:
        assert self.final_manifest and self.target_snapshot and self.review_plan
        try:
            if self.staff_sync is not None:
                desired_players = self.populated_sync.desired if self.populated_sync else draft_apply._desired_players(self.final_manifest)
                player_references = self.populated_sync.references if self.populated_sync else draft_apply.player_references(
                    [row for _member_id, row in self.choices], list(self.final_manifest["desired"]),
                )
                actions = asyncio.run(
                    draft_apply.apply_combined_draft(
                        str(self.final_manifest["matchsheet_id"]), desired_players, player_references, self.staff_sync,
                        self.review_plan, self.target_snapshot, self.apply_confirmation.get(),
                        wedstrijdblad_browser.default_profile_dir(), wedstrijdblad_browser.default_browser_channel(),
                    )
                )
            elif self.populated_sync is None:
                actions = draft_apply.apply_empty_draft_sync(
                    self.final_manifest, [row for _member_id, row in self.choices], self.review_plan,
                    self.target_snapshot, self.apply_confirmation.get(), wedstrijdblad_browser.default_profile_dir(),
                    wedstrijdblad_browser.default_browser_channel(),
                )
            else:
                actions = asyncio.run(
                    draft_apply.apply_populated_draft(
                        str(self.final_manifest["matchsheet_id"]), self.populated_sync, self.review_plan,
                        self.target_snapshot, self.apply_confirmation.get(), wedstrijdblad_browser.default_profile_dir(),
                        wedstrijdblad_browser.default_browser_channel(),
                    )
                )
        except Exception as exc:
            self.after(0, lambda problem=exc: self._apply_failed(problem))
            return
        self.after(0, lambda: self._apply_finished(actions))

    def _apply_failed(self, problem: BaseException) -> None:
        self.apply_button.configure(state="normal")
        self.status.set("Concept is niet toegepast; e-Kickoff of het voorstel wijzigde mogelijk intussen.")
        messagebox.showerror("Concept niet toegepast", str(problem))
        self._offer_support_report("draft-apply", problem)

    def _offer_support_report(self, stage: str, problem: BaseException) -> None:
        """Optionally open a user-reviewed report without retaining diagnostics."""
        if not self.support_opt_in.get():
            return
        try:
            report = support_report.make_support_report(stage, problem)
            opened = support_report.open_reviewed_issue(report)
        except (OSError, ValueError):
            opened = False
        if not opened:
            messagebox.showwarning(
                "Supportformulier niet geopend",
                "Er is geen rapport verstuurd. Controleer je internetverbinding en probeer opnieuw.",
            )

    def _apply_finished(self, actions: tuple) -> None:
        kinds = ", ".join(action if isinstance(action, str) else action.kind for action in actions)
        self.status.set("Concept toegepast en herlezen. Versturen is niet uitgevoerd en bestaat niet in deze app.")
        messagebox.showinfo(
            "Concept toegepast",
            f"Uitgevoerde conceptacties: {kinds}\n\nControleer het wedstrijdblad zelf vóór je ooit de bevestigingstab gebruikt."
        )


class DryRunWizardTests(unittest.TestCase):
    def test_support_url_never_contains_exception_text_or_personal_data(self) -> None:
        secret_detail = "Example Player 01/01/2010 https://private.example/?cookie=secret"
        report = support_report.make_support_report("target-read", RuntimeError(secret_detail))
        url = support_report.issue_url(report)
        self.assertEqual(report.category, "unexpected")
        self.assertNotIn("Example", url)
        self.assertNotIn("2010", url)
        self.assertNotIn("private.example", url)
        self.assertNotIn("secret", url)

    def test_support_report_rejects_unknown_stages(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown"):
            support_report.make_support_report("send", RuntimeError("never"))

    def test_labels_are_position_based_not_serialized_names(self) -> None:
        self.assertEqual(
            DryRunWizard._label(2, psd_reader.RawPlayerRow("4", "Voorbeeld Speler", True, False)),
            "2. #4 Voorbeeld Speler",
        )

    def test_count_gate_accepts_configured_five_substitutes(self) -> None:
        fixture = psd_reader.Fixture("4358637", "06/09/2026", "VC Bertem-leefdaal")
        rows = [psd_reader.RawPlayerRow(str(i), f"P{i}", i <= 11, i > 11) for i in range(1, 17)]
        manifest = psd_reader.make_manifest(fixture, rows, starters=11, substitutes=5)
        self.assertTrue(DryRunWizard._selected_count_matches(manifest))

    def test_platform_default_is_a_supported_channel(self) -> None:
        self.assertIn(psd_reader.default_browser_channel(), {"chrome", "msedge"})
        self.assertIn(wedstrijdblad_browser.default_browser_channel(), {"chrome", "msedge"})

    def test_populated_action_summary_stays_pii_free(self) -> None:
        summary = DryRunWizard._action_counts({"operations": [{"kind": "remove", "member_id": "EK-opaque"}, {"kind": "add", "member_id": "PSD-opaque"}]})
        self.assertEqual(summary, {"remove": 1, "add": 1})

    def test_populated_review_uses_explicit_mapping_and_opaque_plan(self) -> None:
        fixture = psd_reader.Fixture("4358637", "06/09/2026", "VC Bertem-leefdaal")
        rows = [psd_reader.RawPlayerRow("1", "Example One", True, False), psd_reader.RawPlayerRow("2", "Example Two", False, True)]
        raw = psd_reader.make_manifest(fixture, rows, starters=1, substitutes=1)
        manifest = psd_reader.apply_human_roles(raw, raw["desired"][0]["psd_member_id"], raw["desired"][0]["psd_member_id"])
        current = (populated.DiscoveredPlayer("EK-1", "Existing One", "01-01-2010", 1, False, False),)
        choices = (
            populated.MappingChoice(manifest["desired"][0]["psd_member_id"], "EK-1"),
            populated.MappingChoice(manifest["desired"][1]["psd_member_id"], None),
        )
        target = {"mode": "read-only", "matchsheet_id": "4358637", "players_empty": False, "staff_empty": True, "send_disabled": True}
        sync, plan, gate, reviewed_target = prepare_populated_review(manifest, target, rows, current, choices)
        self.assertEqual(len(sync.desired), 2)
        self.assertNotIn("Example", str(plan))
        self.assertTrue(gate.confirmation.startswith("PAS TOE 4358637 "))
        self.assertEqual(reviewed_target["player_state"][0]["member_id"], "EK-1")

    def test_combined_review_binds_opaque_staff_state(self) -> None:
        desired = (draft_apply.executor.KickoffPlayer("PSD-1", 1, True, True),)
        current_staff = (staff_sync.KickoffStaff("EK-S-1", ""),)
        sync = staff_sync.StaffSync(
            (staff_sync.KickoffStaff("EK-S-1", "T1"),), current_staff,
            {"EK-S-1": staff_sync.StaffReference("EK-S-1", "Example Staff", "01-01-1980")},
        )
        target = {
            "mode": "read-only", "matchsheet_id": "4358637", "players_empty": True,
            "staff_empty": False, "send_disabled": True,
        }
        plan, gate, reviewed = prepare_combined_review("4358637", target, desired, (), sync)
        self.assertEqual(reviewed["staff_state"], [{"member_id": "EK-S-1", "function": ""}])
        self.assertTrue(gate.confirmation.startswith("PAS TOE 4358637 "))
        self.assertNotIn("Example", str(plan))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        # Windowed packages deliberately have no reliable console stream.
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(DryRunWizardTests)
        result = unittest.TestResult()
        suite.run(result)
        return 0 if result.wasSuccessful() else 1
    if tk is None:
        parser.error("Tk is unavailable in this Python build; run the packaged Windows executable instead")
    root = tk.Tk()
    DryRunWizard(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
