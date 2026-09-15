#!/usr/bin/env python3
"""Pure, explicitly mapped staff synchronisation planning.

PSD and e-Kickoff do not expose a common staff identifier.  This module never
compares names: the visible GUI must provide one explicit choice for each PSD
staff member.  The resulting plan contains opaque IDs and canonical e-Kickoff
functions only, so it is safe to bind to the review gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
import unittest

import psd_reader
from wedstrijdblad_adf_draft_adapter import AdfDraftAdapterError
from wedstrijdblad_adf_staff_reader import DiscoveredStaff

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ModuleNotFoundError:
    tk = messagebox = ttk = None


class StaffSyncError(AdfDraftAdapterError):
    """The explicit mapping cannot form one safe staff roster."""


@dataclass(frozen=True)
class StaffMappingChoice:
    """A user-visible choice; ``None`` means add as a new e-Kickoff staffer."""

    psd_staff_id: str
    kickoff_staff_id: str | None


@dataclass(frozen=True)
class KickoffStaff:
    """PII-free desired/current staff state used by a deterministic diff."""

    member_id: str
    function: str


@dataclass(frozen=True)
class StaffReference:
    """Ephemeral identity data needed only by the browser adapter."""

    member_id: str
    display_name: str
    birth_date: str | None = None


@dataclass(frozen=True)
class StaffSync:
    desired: tuple[KickoffStaff, ...]
    current: tuple[KickoffStaff, ...]
    references: dict[str, StaffReference]


class StaffMappingDialog:
    """Visible, one-to-one staff identity chooser for the Windows wizard."""

    NEW = "Nieuw toevoegen"

    def __init__(self, master: Any, source_rows: Iterable[psd_reader.RawStaffRow], desired: Iterable[dict[str, str]], current: Iterable[DiscoveredStaff]) -> None:
        if tk is None or ttk is None:
            raise RuntimeError("Tk ontbreekt; gebruik de Windows-executable")
        source = {psd_reader.opaque_staff_id(row.display_name): row for row in source_rows if row.present}
        self._desired = tuple(desired)
        self._current = tuple(current)
        self._values: dict[str, Any] = {}
        self.result: tuple[StaffMappingChoice, ...] | None = None
        self.window = tk.Toplevel(master)
        self.window.title("Bestaande staf koppelen")
        self.window.transient(master)
        self.window.grab_set()
        ttk.Label(
            self.window,
            text="Kies per PSD-staflid een bestaand e-Kickoff-item, of Nieuw toevoegen. Namen worden niet automatisch gekoppeld.",
            wraplength=720,
        ).grid(row=0, column=0, columnspan=2, padx=14, pady=(14, 8), sticky="w")
        self._labels = {self.NEW: None}
        for member in self._current:
            self._labels[f"{member.display_name} ({member.birth_date})"] = member.member_id
        choices = tuple(self._labels)
        for index, item in enumerate(self._desired, start=1):
            source_id = str(item["psd_staff_id"])
            row = source.get(source_id)
            if row is None:
                raise StaffSyncError("PSD-bronrij ontbreekt voor stafkoppelscherm")
            ttk.Label(self.window, text=f"{row.display_name} — gewenste functie: {item['function']}").grid(
                row=index, column=0, padx=14, pady=3, sticky="w"
            )
            value = tk.StringVar(value=self.NEW)
            self._values[source_id] = value
            ttk.Combobox(self.window, textvariable=value, values=choices, state="readonly", width=48).grid(
                row=index, column=1, padx=(8, 14), pady=3, sticky="ew"
            )
        ttk.Button(self.window, text="Verder", command=self._accept).grid(
            row=len(self._desired) + 1, column=1, padx=14, pady=14, sticky="e"
        )
        self.window.columnconfigure(1, weight=1)

    def _accept(self) -> None:
        choices = tuple(StaffMappingChoice(member_id, self._labels[value.get()]) for member_id, value in self._values.items())
        used = [item.kickoff_staff_id for item in choices if item.kickoff_staff_id]
        if len(used) != len(set(used)):
            messagebox.showwarning("Dubbele koppeling", "Eenzelfde e-Kickoff-staflid is meer dan eenmaal gekozen.")
            return
        unchosen = [member for member in self._current if member.member_id not in used]
        if unchosen:
            names = "\n".join(f"• {member.display_name} ({member.birth_date})" for member in unchosen)
            if not messagebox.askyesno(
                "Staf wordt verwijderd",
                "Deze bestaande e-Kickoff-stafleden zijn niet gekoppeld en verschijnen als verwijderactie in het voorstel:\n\n"
                f"{names}\n\nVerdergaan?",
                parent=self.window,
            ):
                return
        self.result = choices
        self.window.destroy()

    def wait(self) -> tuple[StaffMappingChoice, ...] | None:
        self.window.wait_window()
        return self.result


class StaffFunctionDialog:
    """Require the user to review each PSD role → e-Kickoff function choice."""

    def __init__(self, master: Any, source_rows: Iterable[psd_reader.RawStaffRow]) -> None:
        if tk is None or ttk is None:
            raise RuntimeError("Tk ontbreekt; gebruik de Windows-executable")
        self._rows = tuple(row for row in source_rows if row.present)
        self._values: dict[str, Any] = {}
        self.result: dict[str, str] | None = None
        self.window = tk.Toplevel(master)
        self.window.title("PSD-stafrollen bevestigen")
        self.window.transient(master)
        self.window.grab_set()
        ttk.Label(
            self.window,
            text="Controleer de voorgestelde e-Kickoff-functie voor elk aanwezig PSD-staflid. Deze keuze wordt pas in het voorstel opgenomen na Verder.",
            wraplength=720,
        ).grid(row=0, column=0, columnspan=2, padx=14, pady=(14, 8), sticky="w")
        suggestions = psd_reader.suggested_staff_function_choices(self._rows)
        values = tuple(sorted(psd_reader.EKICKOFF_STAFF_FUNCTIONS))
        for index, row in enumerate(self._rows, start=1):
            member_id = psd_reader.opaque_staff_id(row.display_name)
            ttk.Label(self.window, text=f"{row.display_name} — PSD: {row.source_function}").grid(
                row=index, column=0, padx=14, pady=3, sticky="w"
            )
            value = tk.StringVar(value=suggestions.get(member_id, ""))
            self._values[member_id] = value
            ttk.Combobox(self.window, textvariable=value, values=values, state="readonly", width=38).grid(
                row=index, column=1, padx=(8, 14), pady=3, sticky="ew"
            )
        ttk.Button(self.window, text="Stafrollen bevestigen", command=self._accept).grid(
            row=len(self._rows) + 1, column=1, padx=14, pady=14, sticky="e"
        )
        self.window.columnconfigure(1, weight=1)

    def _accept(self) -> None:
        choices = {member_id: value.get() for member_id, value in self._values.items()}
        try:
            for value in choices.values():
                psd_reader.canonical_staff_function(value)
        except psd_reader.SnapshotError as exc:
            messagebox.showwarning("Functie ontbreekt", str(exc))
            return
        self.result = choices
        self.window.destroy()

    def wait(self) -> dict[str, str] | None:
        self.window.wait_window()
        return self.result


def build_staff_sync(
    manifest: dict[str, Any], source_rows: Iterable[psd_reader.RawStaffRow], current_rows: Iterable[DiscoveredStaff],
    choices: Iterable[StaffMappingChoice],
) -> StaffSync:
    """Make an exact staff roster from role-confirmed source and user choices."""
    desired_source = list(manifest.get("desired_staff", ()))
    if manifest.get("staff_policy") != "synchronize":
        raise StaffSyncError("PSD-staf is niet expliciet bevestigd voor synchronisatie")
    source_by_id = {
        psd_reader.opaque_staff_id(row.display_name): row
        for row in source_rows
        if row.present
    }
    desired_ids = [str(item.get("psd_staff_id", "")) for item in desired_source]
    if not desired_ids or len(desired_ids) != len(set(desired_ids)):
        raise StaffSyncError("PSD-stafselectie is leeg of bevat dubbele identiteiten")
    current_rows = tuple(current_rows)
    current_by_id = {row.member_id: row for row in current_rows}
    if len(current_by_id) != len(current_rows):
        raise StaffSyncError("dubbele e-Kickoff-stafidentiteit")

    mapping: dict[str, str | None] = {}
    used_current: set[str] = set()
    for choice in choices:
        if choice.psd_staff_id not in desired_ids or choice.psd_staff_id in mapping:
            raise StaffSyncError("ongeldige of dubbele PSD-stafkoppelkeuze")
        if choice.kickoff_staff_id is not None:
            if choice.kickoff_staff_id not in current_by_id or choice.kickoff_staff_id in used_current:
                raise StaffSyncError("ongeldige of dubbele e-Kickoff-stafkoppelkeuze")
            used_current.add(choice.kickoff_staff_id)
        mapping[choice.psd_staff_id] = choice.kickoff_staff_id
    if set(mapping) != set(desired_ids):
        raise StaffSyncError("voor elk aanwezig PSD-staflid is een expliciete koppelkeuze vereist")

    references: dict[str, StaffReference] = {
        current.member_id: StaffReference(current.member_id, current.display_name, current.birth_date)
        for current in current_rows
    }
    desired: list[KickoffStaff] = []
    for item in desired_source:
        psd_id = str(item["psd_staff_id"])
        try:
            function = psd_reader.canonical_staff_function(str(item["function"]))
        except (KeyError, psd_reader.SnapshotError) as exc:
            raise StaffSyncError("PSD-staffunctie is niet eenduidig bevestigd") from exc
        chosen = mapping[psd_id]
        if chosen is None:
            try:
                source = source_by_id[psd_id]
            except KeyError as exc:
                raise StaffSyncError("PSD-bronrij ontbreekt voor nieuw staflid") from exc
            target_id = psd_id
            references[target_id] = StaffReference(target_id, source.display_name)
        else:
            target_id = chosen
        desired.append(KickoffStaff(target_id, function))

    return StaffSync(
        tuple(sorted(desired, key=lambda staff: staff.member_id)),
        tuple(sorted((KickoffStaff(item.member_id, item.function) for item in current_rows), key=lambda staff: staff.member_id)),
        references,
    )


def make_staff_actions(sync: StaffSync) -> tuple[dict[str, str], ...]:
    """Plan sequential add/remove/function actions with opaque IDs only."""
    desired = {staff.member_id: staff for staff in sync.desired}
    current = {staff.member_id: staff for staff in sync.current}
    actions: list[dict[str, str]] = []
    actions.extend({"kind": "staff_remove", "member_id": member_id} for member_id in sorted(set(current) - set(desired)))
    actions.extend({"kind": "staff_add", "member_id": member_id} for member_id in sorted(set(desired) - set(current)))
    actions.extend(
        {"kind": "staff_function", "member_id": member_id, "function": desired[member_id].function}
        for member_id in sorted(desired)
        if member_id not in current or current[member_id].function != desired[member_id].function
    )
    return tuple(actions)


class StaffSyncTests(unittest.TestCase):
    def manifest(self) -> dict[str, Any]:
        return {
            "staff_policy": "synchronize",
            "desired_staff": [
                {"psd_staff_id": "PSD-S-1", "function": "T1"},
                {"psd_staff_id": "PSD-S-2", "function": "Official team delegate"},
            ],
        }

    def source(self) -> tuple[psd_reader.RawStaffRow, ...]:
        return (
            psd_reader.RawStaffRow("Example Coach", "Trainer", True),
            psd_reader.RawStaffRow("Example Delegate", "Delegate", True),
        )

    def test_explicit_existing_and_new_staff_mapping_makes_pii_free_actions(self) -> None:
        source = self.source()
        manifest = self.manifest() | {
            "desired_staff": [
                {"psd_staff_id": psd_reader.opaque_staff_id(source[0].display_name), "function": "T1"},
                {"psd_staff_id": psd_reader.opaque_staff_id(source[1].display_name), "function": "Official team delegate"},
            ]
        }
        current = (DiscoveredStaff("EK-S-1", "Current Coach", "01-01-1980", ""),)
        sync = build_staff_sync(
            manifest, source, current,
            (
                StaffMappingChoice(manifest["desired_staff"][0]["psd_staff_id"], "EK-S-1"),
                StaffMappingChoice(manifest["desired_staff"][1]["psd_staff_id"], None),
            ),
        )
        actions = make_staff_actions(sync)
        self.assertEqual([action["kind"] for action in actions], ["staff_add", "staff_function", "staff_function"])
        self.assertNotIn("Example", str(actions))

    def test_duplicate_existing_staff_choice_is_rejected(self) -> None:
        source = self.source()
        manifest = self.manifest() | {
            "desired_staff": [
                {"psd_staff_id": psd_reader.opaque_staff_id(source[0].display_name), "function": "T1"},
                {"psd_staff_id": psd_reader.opaque_staff_id(source[1].display_name), "function": "Official team delegate"},
            ]
        }
        current = (DiscoveredStaff("EK-S-1", "Current Coach", "01-01-1980", ""),)
        with self.assertRaisesRegex(StaffSyncError, "dubbele e-Kickoff"):
            build_staff_sync(
                manifest, source, current,
                (
                    StaffMappingChoice(manifest["desired_staff"][0]["psd_staff_id"], "EK-S-1"),
                    StaffMappingChoice(manifest["desired_staff"][1]["psd_staff_id"], "EK-S-1"),
                ),
            )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(StaffSyncTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
