#!/usr/bin/env python3
"""Deterministic bridge for synchronising a populated e-Kickoff player table.

The mapping dialog (to be hosted by the Windows wizard) passes only explicit
choices.  It never fuzzy-matches display names.  Presentation names and birth
dates are input-only, live-window data; this module emits opaque IDs and draft
executor state only.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any, Iterable

import psd_reader
from wedstrijdblad_adf_draft_adapter import DiscoveredPlayer, PlayerReference
from wedstrijdblad_draft_executor import DraftExecutionError, KickoffPlayer, make_actions

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ModuleNotFoundError:
    tk = messagebox = None
    ttk = None


@dataclass(frozen=True)
class MappingChoice:
    """One explicit user selection; ``None`` means add the PSD player anew."""

    psd_member_id: str
    kickoff_member_id: str | None


@dataclass(frozen=True)
class PopulatedSync:
    desired: tuple[KickoffPlayer, ...]
    current: tuple[KickoffPlayer, ...]
    references: dict[str, PlayerReference]


class MappingDialog:
    """Small modal chooser; names are presentation-only and never persisted."""

    NEW = "Nieuw toevoegen"

    def __init__(self, master: Any, source_rows: Iterable[psd_reader.RawPlayerRow], desired: Iterable[dict[str, Any]], current: Iterable[DiscoveredPlayer]) -> None:
        if tk is None or ttk is None:
            raise RuntimeError("Tk ontbreekt; gebruik de Windows-executable")
        source = {psd_reader.opaque_member_id(row.display_name): row for row in source_rows}
        self._desired = list(desired)
        self._current = tuple(current)
        self._vars: dict[str, Any] = {}
        self.result: tuple[MappingChoice, ...] | None = None
        self.window = tk.Toplevel(master)
        self.window.title("Bestaande spelers koppelen")
        self.window.transient(master)
        self.window.grab_set()
        ttk.Label(self.window, text="Kies per PSD-speler een bestaand blad-item, of Nieuw toevoegen.", wraplength=680).grid(row=0, column=0, columnspan=2, padx=14, pady=(14, 8), sticky="w")
        self._labels = {self.NEW: None}
        for player in self._current:
            label = f"{player.display_name} ({player.birth_date})"
            self._labels[label] = player.member_id
        values = tuple(self._labels)
        for index, item in enumerate(self._desired, start=1):
            member_id = str(item["psd_member_id"])
            row = source.get(member_id)
            if row is None:
                raise DraftExecutionError("PSD-bronrij ontbreekt voor koppelscherm")
            ttk.Label(self.window, text=f"#{row.shirt_number} {row.display_name}").grid(row=index, column=0, padx=14, pady=3, sticky="w")
            value = tk.StringVar(value=self.NEW)
            self._vars[member_id] = value
            ttk.Combobox(self.window, textvariable=value, values=values, state="readonly", width=44).grid(row=index, column=1, padx=(8, 14), pady=3, sticky="ew")
        ttk.Button(self.window, text="Verder", command=self._accept).grid(row=len(self._desired) + 1, column=1, padx=14, pady=14, sticky="e")
        self.window.columnconfigure(1, weight=1)

    def _accept(self) -> None:
        choices = tuple(MappingChoice(member_id, self._labels[value.get()]) for member_id, value in self._vars.items())
        used = [choice.kickoff_member_id for choice in choices if choice.kickoff_member_id]
        if len(used) != len(set(used)):
            messagebox.showwarning("Dubbele koppeling", "Een bestaande e-Kickoff-speler is meer dan eenmaal gekozen.")
            return
        unchosen = [player for player in self._current if player.member_id not in used]
        if unchosen:
            names = "\n".join(f"• {player.display_name} ({player.birth_date})" for player in unchosen)
            if not messagebox.askyesno(
                "Spelers worden verwijderd",
                "Deze bestaande e-Kickoff-spelers zijn niet gekoppeld en verschijnen als verwijderactie in het voorstel:\n\n"
                f"{names}\n\nVerdergaan?",
                parent=self.window,
            ):
                return
        self.result = choices
        self.window.destroy()

    def wait(self) -> tuple[MappingChoice, ...] | None:
        self.window.wait_window()
        return self.result


def build_populated_sync(
    manifest: dict[str, Any], source_rows: Iterable[psd_reader.RawPlayerRow], current_rows: Iterable[DiscoveredPlayer],
    choices: Iterable[MappingChoice],
) -> PopulatedSync:
    """Turn explicit mapping choices into a complete diff-ready roster.

    Every selected PSD player must receive exactly one choice.  A current
    e-Kickoff row may be chosen at most once.  Current rows left unchosen are
    preserved in ``current`` so the executor's normal set diff proposes their
    removal visibly; nothing is deleted in this function.
    """
    desired_source = list(manifest.get("desired", ()))
    source_by_id = {psd_reader.opaque_member_id(row.display_name): row for row in source_rows}
    desired_ids = [str(player["psd_member_id"]) for player in desired_source]
    current_rows = tuple(current_rows)
    current_by_id = {row.member_id: row for row in current_rows}
    if len(current_by_id) != len(current_rows):
        raise DraftExecutionError("dubbele e-Kickoff-ID in doelblad")
    choice_by_psd: dict[str, str | None] = {}
    used_current: set[str] = set()
    for choice in choices:
        if choice.psd_member_id not in desired_ids or choice.psd_member_id in choice_by_psd:
            raise DraftExecutionError("ongeldige of dubbele PSD-koppelkeuze")
        if choice.kickoff_member_id is not None:
            if choice.kickoff_member_id not in current_by_id or choice.kickoff_member_id in used_current:
                raise DraftExecutionError("ongeldige of dubbele e-Kickoff-koppelkeuze")
            used_current.add(choice.kickoff_member_id)
        choice_by_psd[choice.psd_member_id] = choice.kickoff_member_id
    if set(choice_by_psd) != set(desired_ids):
        raise DraftExecutionError("voor elke PSD-speler is een expliciete koppelkeuze vereist")

    references: dict[str, PlayerReference] = {
        row.member_id: PlayerReference(row.member_id, row.display_name, row.birth_date) for row in current_by_id.values()
    }
    desired: list[KickoffPlayer] = []
    for source in desired_source:
        psd_id = str(source["psd_member_id"])
        chosen = choice_by_psd[psd_id]
        if chosen is None:
            try:
                row = source_by_id[psd_id]
            except KeyError as exc:
                raise DraftExecutionError("PSD-bronrij ontbreekt voor nieuwe speler") from exc
            target_id = psd_id
            references[target_id] = PlayerReference(target_id, row.display_name)
        else:
            target_id = chosen
        desired.append(KickoffPlayer(target_id, int(source["shirt_number"]), bool(source["captain"]), bool(source["goalkeeper"])))
    current = tuple(
        KickoffPlayer(row.member_id, row.shirt_number, row.captain, row.goalkeeper) for row in current_by_id.values()
    )
    return PopulatedSync(tuple(desired), current, references)


def make_populated_plan(matchsheet_id: str, sync: PopulatedSync) -> dict[str, Any]:
    """Render the exact executor actions without display data for review/gating."""
    if not matchsheet_id.isdigit():
        raise DraftExecutionError("ongeldig wedstrijdblad-ID")
    actions = make_actions(sync.desired, sync.current)
    return {
        "matchsheet_id": matchsheet_id,
        "operations": [
            {"kind": action.kind, **({"member_id": action.member_id} if action.member_id else {}),
             **({"number": action.number} if action.number is not None else {})}
            for action in actions
        ],
    }


class PopulatedSyncTests(unittest.TestCase):
    def manifest(self) -> dict[str, Any]:
        rows = [psd_reader.RawPlayerRow("1", "Example One", True, False), psd_reader.RawPlayerRow("2", "Example Two", True, False)]
        raw = psd_reader.make_manifest(psd_reader.Fixture("4358637", "06/09/2026", "Example"), rows, starters=2, substitutes=0)
        return psd_reader.apply_human_roles(raw, raw["desired"][0]["psd_member_id"], raw["desired"][0]["psd_member_id"])

    def test_explicit_existing_and_new_mapping_builds_a_diff_ready_roster(self) -> None:
        manifest = self.manifest()
        source = [psd_reader.RawPlayerRow("1", "Example One", True, False), psd_reader.RawPlayerRow("2", "Example Two", True, False)]
        current = (DiscoveredPlayer("EK-1", "Existing", "01-01-2010", 8, False, False),)
        synced = build_populated_sync(
            manifest, source, current,
            (MappingChoice(manifest["desired"][0]["psd_member_id"], "EK-1"), MappingChoice(manifest["desired"][1]["psd_member_id"], None)),
        )
        self.assertEqual({player.kickoff_member_id for player in synced.desired}, {"EK-1", manifest["desired"][1]["psd_member_id"]})
        self.assertIn("EK-1", synced.references)

    def test_missing_or_duplicate_choice_is_rejected(self) -> None:
        manifest = self.manifest()
        source = [psd_reader.RawPlayerRow("1", "Example One", True, False), psd_reader.RawPlayerRow("2", "Example Two", True, False)]
        with self.assertRaisesRegex(DraftExecutionError, "expliciete"):
            build_populated_sync(manifest, source, (), (MappingChoice(manifest["desired"][0]["psd_member_id"], None),))

    def test_unmapped_existing_player_remains_in_current_for_visible_removal_diff(self) -> None:
        manifest = self.manifest()
        source = [psd_reader.RawPlayerRow("1", "Example One", True, False), psd_reader.RawPlayerRow("2", "Example Two", True, False)]
        current = (
            DiscoveredPlayer("EK-1", "Existing One", "01-01-2010", 8, False, False),
            DiscoveredPlayer("EK-OLD", "Existing Old", "02-02-2010", 9, False, False),
        )
        synced = build_populated_sync(
            manifest, source, current,
            (MappingChoice(manifest["desired"][0]["psd_member_id"], "EK-1"), MappingChoice(manifest["desired"][1]["psd_member_id"], None)),
        )
        self.assertEqual({player.kickoff_member_id for player in synced.current}, {"EK-1", "EK-OLD"})

    def test_plan_contains_only_opaque_actions(self) -> None:
        manifest = self.manifest()
        source = [psd_reader.RawPlayerRow("1", "Example One", True, False), psd_reader.RawPlayerRow("2", "Example Two", True, False)]
        synced = build_populated_sync(manifest, source, (), tuple(MappingChoice(item["psd_member_id"], None) for item in manifest["desired"]))
        plan = make_populated_plan("4358637", synced)
        self.assertEqual(plan["matchsheet_id"], "4358637")
        self.assertNotIn("Example", str(plan))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PopulatedSyncTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
