#!/usr/bin/env python3
"""End-to-end, no-write PSD -> e-Kickoff dry run for an empty target sheet.

This coordinator deliberately supports only the one state proven safe today:
an empty e-Kickoff player sheet.  It reads PSD, has the user explicitly select
captain and goalkeeper, reads e-Kickoff, and creates a deterministic local
proposal.  A non-empty e-Kickoff player sheet stops the run rather than
guessing identities or removing anyone.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any

import psd_reader
import wedstrijdblad_browser
import wedstrijdblad_execution_guard as execution_guard
import wedstrijdblad_sync as planner


class DryRunBlocked(RuntimeError):
    """The current state is outside the carefully verified no-write path."""


def plan_empty_target(manifest: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Plan only if the live reader verified the e-Kickoff player set is empty."""
    if manifest.get("matchsheet_id") != target.get("matchsheet_id"):
        raise DryRunBlocked("PSD- en e-Kickoff-wedstrijdblad-ID verschillen")
    if target.get("players_empty") is not True:
        raise DryRunBlocked(
            "e-Kickoff bevat al spelers. Stop zonder plan: identiteitskoppeling moet eerst expliciet worden gevalideerd."
        )
    # The reader's empty-state assertion is sufficient to construct the one
    # exact roster representation for this narrow empty-sheet path.
    target = {**target, "player_state": []}
    desired = tuple(planner.Player.from_mapping(player) for player in manifest["desired"])
    rules = planner.SelectionRules.from_mapping(manifest["selection_rules"])
    result = planner.make_plan(
        str(manifest["matchsheet_id"]),
        desired,
        tuple(),
        tuple(),
        tuple(),
        rules,
        "preserve",
    )
    # This is still only a dry-run.  The gate binds a future, separately
    # authorised draft writer to this exact plan and target state; it does not
    # enable writing here.
    result["review_gate"] = execution_guard.create_review_gate(result, target).as_mapping()
    return result


class DryRunTests(unittest.TestCase):
    def manifest(self) -> dict[str, Any]:
        fixture = psd_reader.Fixture("4358637", "20/09/2026", "Oud-heverlee Leuven")
        rows = [
            psd_reader.RawPlayerRow(str(index), f"Example Player {index}", index <= 11, index > 11)
            for index in range(1, 15)
        ]
        raw = psd_reader.make_manifest(fixture, rows)
        return psd_reader.apply_human_roles(raw, raw["desired"][0]["psd_member_id"], raw["desired"][0]["psd_member_id"])

    @staticmethod
    def empty_target() -> dict[str, Any]:
        return {
            "mode": "read-only",
            "matchsheet_id": "4358637",
            "players_empty": True,
            "staff_empty": True,
            "send_disabled": True,
            "player_state": [],
        }

    def test_empty_target_produces_14_additions(self) -> None:
        result = plan_empty_target(self.manifest(), self.empty_target())
        self.assertEqual(len(result["operations"]["players"]["add"]), 14)
        self.assertEqual(result["staff_policy"], "preserve")
        self.assertIn("review_gate", result)

    def test_nonempty_target_stops_before_any_plan(self) -> None:
        with self.assertRaisesRegex(DryRunBlocked, "identiteitskoppeling"):
            plan_empty_target(self.manifest(), self.empty_target() | {"players_empty": False})

    def test_protected_sheet_stops_in_planner(self) -> None:
        manifest = self.manifest()
        manifest["matchsheet_id"] = "4344375"
        with self.assertRaises(planner.ValidationError):
            plan_empty_target(manifest, self.empty_target() | {"matchsheet_id": "4344375"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matchsheet_id", nargs="?", help="e-Kickoff wedstrijdblad-ID")
    parser.add_argument("--date", help="PSD-wedstrijddatum DD/MM/JJJJ")
    parser.add_argument("--opponent", help="PSD-tegenstander zoals zichtbaar op het dashboard")
    parser.add_argument("--output", type=Path, help="optioneel lokaal JSON-bestand voor het dry-run-voorstel")
    parser.add_argument("--starters", type=int, default=11, help="vereist aantal basisspelers (standaard: 11)")
    parser.add_argument("--substitutes", type=int, default=3, help="vereist aantal wisselspelers (standaard: 3)")
    parser.add_argument("--channel", default=psd_reader.default_browser_channel(), choices=("msedge", "chrome"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(DryRunTests)
        )
        return 0 if result.wasSuccessful() else 1
    if not all((args.matchsheet_id, args.date, args.opponent)):
        parser.error("matchsheet_id, --date en --opponent zijn vereist")
    fixture = psd_reader.Fixture(str(args.matchsheet_id), args.date, args.opponent)
    try:
        fixture.validate()
        manifest, rows = asyncio.run(
            psd_reader.read_psd_snapshot(
                fixture, psd_reader.default_profile_dir(), args.channel,
                starters=args.starters, substitutes=args.substitutes,
            )
        )
        manifest = psd_reader.confirm_roles_interactively(rows, manifest, fixture.matchsheet_id)
        print("\nPSD-preflight:")
        desired = tuple(planner.Player.from_mapping(player) for player in manifest["desired"])
        print(planner.render_preflight(fixture.matchsheet_id, desired, tuple(), staff_policy="preserve"))
        input("Druk Enter om e-Kickoff uitsluitend te lezen (geen gegevens worden gewijzigd): ")
        target = asyncio.run(
            wedstrijdblad_browser.read_matchsheet(
                fixture.matchsheet_id, wedstrijdblad_browser.default_profile_dir(), args.channel
            )
        )
        result = plan_empty_target(manifest, target)
        encoded = json.dumps(result, indent=2, sort_keys=True)
        print("\nDRY-RUN voorstel (geen browseractie):")
        print(encoded)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded + "\n", encoding="utf-8")
            print(f"Alleen lokaal voorstel geschreven naar {args.output}")
    except KeyboardInterrupt:
        print("Dry-run geannuleerd; er is niets gewijzigd", file=sys.stderr)
        return 130
    except Exception as exc:
        # A browser timeout or login navigation failure is a normal retryable
        # operational condition here.  There is no apply path to roll back.
        print(f"Geen dry-run gemaakt: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
