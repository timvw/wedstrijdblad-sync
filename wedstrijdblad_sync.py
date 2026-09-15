#!/usr/bin/env python3
"""Deterministische, bevestigings-gated planner voor PSD -> e-Kickoff.

The program deliberately has no browser, HTTP or ADF write capability. It always
prints a preflight first, refuses to guess ambiguous captain/goalkeeper/staff data,
and only emits a reconciliation plan after an explicit, match-specific confirmation.

The selection rule is data, not code: different competitions can require a different
number of starters/substitutes.  Staff is either explicitly synchronized or explicitly
preserved.  An empty PSD staff list never silently removes staff from e-Kickoff.
"""

from __future__ import annotations

import argparse
import json
import sys
import unittest
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


PROTECTED_MATCHSHEET_IDS = frozenset({"4344375"})


class ValidationError(ValueError):
    """Input cannot safely be reconciled."""


class PreflightBlocked(ValidationError):
    """A human choice is required before a plan can be formed."""


@dataclass(frozen=True)
class SelectionRules:
    """The match-specific rules supplied by the club/competition configuration."""

    starters: int
    substitutes: int
    captain_required: bool = True
    captain_must_start: bool = True
    goalkeeper_required: bool = True

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "SelectionRules":
        # Kept only as a backwards-compatible default for the established VK Linden
        # U17 workflow.  A production adapter must write this explicitly in its plan.
        if value is None:
            return cls(11, 3)
        required = {"starters", "substitutes"}
        missing = required - value.keys()
        if missing:
            raise ValidationError(f"selection_rules missing fields: {sorted(missing)}")
        rules = cls(
            value["starters"],
            value["substitutes"],
            value.get("captain_required", True),
            value.get("captain_must_start", True),
            value.get("goalkeeper_required", True),
        )
        if type(rules.starters) is not int or type(rules.substitutes) is not int:
            raise ValidationError("selection_rules starters and substitutes must be integers")
        if not 0 <= rules.starters <= 25 or not 0 <= rules.substitutes <= 25:
            raise ValidationError("selection_rules counts must be between 0 and 25")
        if rules.starters + rules.substitutes == 0:
            raise ValidationError("selection_rules must require at least one player")
        if any(type(flag) is not bool for flag in
               (rules.captain_required, rules.captain_must_start, rules.goalkeeper_required)):
            raise ValidationError("selection_rules flags must be boolean")
        if rules.captain_must_start and not rules.captain_required:
            raise ValidationError("captain_must_start requires captain_required")
        return rules


VALID_STAFF_POLICIES = frozenset({"synchronize", "preserve"})


@dataclass(frozen=True)
class Player:
    psd_member_id: str
    shirt_number: int
    role: str
    captain: bool
    goalkeeper: bool | None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Player":
        required = {"psd_member_id", "shirt_number", "role", "captain"}
        missing = required - value.keys()
        if missing:
            raise ValidationError(f"player missing fields: {sorted(missing)}")
        goalkeeper = value.get("goalkeeper")
        if goalkeeper is not None and type(goalkeeper) is not bool:
            raise ValidationError(f"goalkeeper must be boolean or null for {value['psd_member_id']}")
        player = cls(str(value["psd_member_id"]), value["shirt_number"], str(value["role"]), value["captain"], goalkeeper)
        if not player.psd_member_id:
            raise ValidationError("empty psd_member_id")
        if type(player.shirt_number) is not int or not 1 <= player.shirt_number <= 99:
            raise ValidationError(f"invalid shirt number for {player.psd_member_id}")
        if player.role not in {"starter", "substitute"}:
            raise ValidationError(f"invalid role for {player.psd_member_id}: {player.role}")
        if type(player.captain) is not bool:
            raise ValidationError(f"captain must be boolean for {player.psd_member_id}")
        return player


@dataclass(frozen=True)
class StaffMember:
    psd_staff_id: str
    function: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "StaffMember":
        required = {"psd_staff_id", "function"}
        missing = required - value.keys()
        if missing:
            raise ValidationError(f"staff member missing fields: {sorted(missing)}")
        member = cls(str(value["psd_staff_id"]), str(value["function"]).strip())
        if not member.psd_staff_id or not member.function:
            raise ValidationError("staff member requires non-empty ID and function")
        return member


def _players(values: Iterable[dict[str, Any]]) -> tuple[Player, ...]:
    return tuple(Player.from_mapping(value) for value in values)


def _staff(values: Iterable[dict[str, Any]]) -> tuple[StaffMember, ...]:
    return tuple(StaffMember.from_mapping(value) for value in values)


def preflight_questions(desired: tuple[Player, ...], desired_staff: tuple[StaffMember, ...],
                        rules: SelectionRules | None = None, staff_policy: str = "synchronize",
                        allow_empty_staff: bool = False) -> list[str]:
    """Return every blocking ambiguity rather than silently repairing any of them."""
    rules = rules or SelectionRules.from_mapping(None)
    if staff_policy not in VALID_STAFF_POLICIES:
        raise ValidationError(f"invalid staff_policy: {staff_policy}")
    if type(allow_empty_staff) is not bool:
        raise ValidationError("allow_empty_staff must be boolean")
    questions: list[str] = []
    ids = [player.psd_member_id for player in desired]
    expected_players = rules.starters + rules.substitutes
    if len(desired) != expected_players:
        questions.append(f"exact {expected_players} spelers vereist; PSD levert er nu {len(desired)}")
    if len(set(ids)) != len(ids):
        questions.append("PSD bevat hetzelfde lid meer dan één keer")
    numbers = [player.shirt_number for player in desired]
    if len(set(numbers)) != len(numbers):
        questions.append("dubbel rugnummer in PSD; kies expliciet een tijdelijk nummer")
    starters = sum(player.role == "starter" for player in desired)
    substitutes = sum(player.role == "substitute" for player in desired)
    if (starters, substitutes) != (rules.starters, rules.substitutes):
        questions.append(f"{rules.starters} basisspelers + {rules.substitutes} wissels vereist; gevonden: {starters} + {substitutes}")
    captains = [player.psd_member_id for player in desired if player.captain]
    if rules.captain_required and len(captains) != 1:
        questions.append(f"kapitein onduidelijk: exact één vereist, gevonden: {len(captains)}")
    elif rules.captain_must_start and captains and next(player for player in desired if player.captain).role != "starter":
        questions.append("kapitein moet een basisspeler zijn")
    goalkeeper_values = [player.goalkeeper for player in desired]
    if rules.goalkeeper_required and any(value is None for value in goalkeeper_values):
        questions.append("doelman is niet expliciet bepaald voor elke geselecteerde speler")
    elif rules.goalkeeper_required and sum(goalkeeper_values) != 1:
        questions.append(f"doelman onduidelijk: exact één vereist, gevonden: {sum(goalkeeper_values)}")
    staff_ids = [member.psd_staff_id for member in desired_staff]
    if staff_policy == "synchronize" and not desired_staff and not allow_empty_staff:
        questions.append("technische staf ontbreekt; kies staf, behoud huidige staf, of keur expliciet een lege staflijst goed")
    elif staff_policy == "synchronize" and len(set(staff_ids)) != len(staff_ids):
        questions.append("technische staf bevat een dubbel lid")
    return questions


def _require_clean_preflight(desired: tuple[Player, ...], desired_staff: tuple[StaffMember, ...],
                             rules: SelectionRules, staff_policy: str, allow_empty_staff: bool) -> None:
    questions = preflight_questions(desired, desired_staff, rules, staff_policy, allow_empty_staff)
    if questions:
        raise PreflightBlocked("; ".join(questions))


def make_plan(matchsheet_id: str, desired: tuple[Player, ...], current: tuple[Player, ...],
              desired_staff: tuple[StaffMember, ...], current_staff: tuple[StaffMember, ...],
              rules: SelectionRules | None = None, staff_policy: str = "synchronize",
              allow_empty_staff: bool = False) -> dict[str, Any]:
    """Return a stable plan. This function has no external side effects."""
    matchsheet_id = str(matchsheet_id)
    if matchsheet_id in PROTECTED_MATCHSHEET_IDS:
        raise ValidationError(f"match sheet {matchsheet_id} is protected and must never be touched")
    rules = rules or SelectionRules.from_mapping(None)
    _require_clean_preflight(desired, desired_staff, rules, staff_policy, allow_empty_staff)
    current_ids = [player.psd_member_id for player in current]
    if len(set(current_ids)) != len(current_ids):
        raise ValidationError("current match sheet has duplicate PSD member IDs; require manual review")
    desired_by_id = {player.psd_member_id: player for player in desired}
    current_by_id = {player.psd_member_id: player for player in current}
    player_remove = sorted(set(current_by_id) - set(desired_by_id))
    player_add = [asdict(player) for player in sorted(
        (player for player in desired if player.psd_member_id not in current_by_id),
        key=lambda player: (player.shirt_number, player.psd_member_id),
    )]
    player_update = []
    for member_id in sorted(set(current_by_id) & set(desired_by_id)):
        before, after = current_by_id[member_id], desired_by_id[member_id]
        changes = {field: getattr(after, field) for field in ("shirt_number", "role", "captain", "goalkeeper")
                   if getattr(before, field) != getattr(after, field)}
        if changes:
            player_update.append({"psd_member_id": member_id, "set": changes})
    if staff_policy == "preserve":
        staff_remove: list[str] = []
        staff_add: list[dict[str, Any]] = []
        staff_update: list[dict[str, Any]] = []
    else:
        desired_staff_by_id = {member.psd_staff_id: member for member in desired_staff}
        current_staff_by_id = {member.psd_staff_id: member for member in current_staff}
        staff_remove = sorted(set(current_staff_by_id) - set(desired_staff_by_id))
        staff_add = [asdict(member) for member in sorted(
            (member for member in desired_staff if member.psd_staff_id not in current_staff_by_id),
            key=lambda member: (member.function, member.psd_staff_id),
        )]
        staff_update = [{"psd_staff_id": staff_id, "set": {"function": desired_staff_by_id[staff_id].function}}
                        for staff_id in sorted(set(current_staff_by_id) & set(desired_staff_by_id))
                        if current_staff_by_id[staff_id].function != desired_staff_by_id[staff_id].function]
    operations = {"players": {"remove": player_remove, "add": player_add, "update": player_update},
                  "staff": {"remove": staff_remove, "add": staff_add, "update": staff_update}}
    changed = any(operations[group][action] for group in operations for action in operations[group])
    return {"mode": "dry-run-only", "matchsheet_id": matchsheet_id,
            "selection_rules": asdict(rules), "staff_policy": staff_policy,
            "in_sync": not changed,
            "operations": operations,
            "next_step": "no action required" if not changed else "reviewed diff ready; an ADF adapter still requires a separate explicit write approval"}


def load_manifest(path: Path) -> tuple[str, tuple[Player, ...], tuple[Player, ...], tuple[StaffMember, ...], tuple[StaffMember, ...], SelectionRules, str, bool]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValidationError("top-level JSON must be an object")
    try:
        staff_policy = str(raw.get("staff_policy", "synchronize"))
        allow_empty_staff = raw.get("allow_empty_staff", False)
        if staff_policy not in VALID_STAFF_POLICIES:
            raise ValidationError(f"invalid staff_policy: {staff_policy}")
        if type(allow_empty_staff) is not bool:
            raise ValidationError("allow_empty_staff must be boolean")
        return (str(raw["matchsheet_id"]), _players(raw["desired"]), _players(raw["current"]),
                _staff(raw.get("desired_staff", [])), _staff(raw.get("current_staff", [])),
                SelectionRules.from_mapping(raw.get("selection_rules")), staff_policy, allow_empty_staff)
    except KeyError as exc:
        raise ValidationError(f"missing required top-level field: {exc.args[0]}") from exc


def render_preflight(matchsheet_id: str, desired: tuple[Player, ...], desired_staff: tuple[StaffMember, ...],
                     rules: SelectionRules | None = None, staff_policy: str = "synchronize",
                     allow_empty_staff: bool = False) -> str:
    """Human-first summary. IDs are intentional: adapters may render names transiently."""
    captain = next((player.psd_member_id for player in desired if player.captain), "onbepaald")
    goalkeeper = next((player.psd_member_id for player in desired if player.goalkeeper is True), "onbepaald")
    starters = sum(player.role == "starter" for player in desired)
    substitutes = sum(player.role == "substitute" for player in desired)
    player_list = ", ".join(
        f"{player.psd_member_id}#{player.shirt_number}[{player.role}{', C' if player.captain else ''}{', GK' if player.goalkeeper else ''}]"
        for player in sorted(desired, key=lambda player: (player.shirt_number, player.psd_member_id)))
    staff_list = ", ".join(f"{member.psd_staff_id}[{member.function}]" for member in desired_staff) or "(geen)"
    rules = rules or SelectionRules.from_mapping(None)
    lines = [f"PRE-FLIGHT wedstrijdblad {matchsheet_id}",
             f"Regel: {rules.starters} basis + {rules.substitutes} wissels"
             f"; kapitein={'ja' if rules.captain_required else 'nee'}"
             f"; doelman={'ja' if rules.goalkeeper_required else 'nee'}",
             f"Spelers: {len(desired)} totaal ({starters} basis, {substitutes} wissels)",
             f"  {player_list}", f"Kapitein: {captain}", f"Doelman: {goalkeeper}",
             f"Staf: {staff_policy} ({len(desired_staff)} totaal)", f"  {staff_list}"]
    questions = preflight_questions(desired, desired_staff, rules, staff_policy, allow_empty_staff)
    if questions:
        lines.extend(["BLOKKEERT — menselijke beslissing nodig:", *[f"  - {question}" for question in questions]])
    else:
        lines.append(f"Bevestig met exact: BEVESTIG {matchsheet_id}")
    return "\n".join(lines)


class PlannerTests(unittest.TestCase):
    def desired(self) -> tuple[Player, ...]:
        return tuple(Player(f"P{index:02}", index, "starter" if index <= 11 else "substitute", index == 1, index == 1)
                     for index in range(1, 15))

    def staff(self) -> tuple[StaffMember, ...]:
        return (StaffMember("S01", "coach"),)

    def test_protected_match_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "protected"):
            make_plan("4344375", self.desired(), tuple(), self.staff(), tuple())

    def test_diff_is_deterministic(self) -> None:
        current = tuple(player for player in self.desired() if player.psd_member_id != "P14")
        plan = make_plan("4358637", self.desired(), current, self.staff(), tuple())
        self.assertFalse(plan["in_sync"])
        self.assertEqual(plan["operations"]["players"]["add"], [asdict(self.desired()[-1])])

    def test_duplicate_number_is_blocked(self) -> None:
        invalid = list(self.desired())
        invalid[-1] = Player("P14", 1, "substitute", False, False)
        self.assertTrue(any("dubbel rugnummer" in question for question in preflight_questions(tuple(invalid), self.staff())))

    def test_incomplete_bench_is_blocked(self) -> None:
        self.assertTrue(any("exact 14" in question for question in preflight_questions(self.desired()[:-2], self.staff())))

    def test_missing_goalkeeper_is_blocked(self) -> None:
        unclear = tuple(Player(p.psd_member_id, p.shirt_number, p.role, p.captain, None) for p in self.desired())
        self.assertTrue(any("doelman" in question for question in preflight_questions(unclear, self.staff())))

    def test_competition_rule_is_configurable(self) -> None:
        rules = SelectionRules.from_mapping({"starters": 7, "substitutes": 2})
        selected = tuple(Player(f"P{index:02}", index, "starter" if index <= 7 else "substitute",
                                index == 1, index == 1)
                         for index in range(1, 10))
        self.assertEqual(preflight_questions(selected, self.staff(), rules), [])

    def test_preserve_staff_never_removes_existing_staff(self) -> None:
        plan = make_plan("4358637", self.desired(), tuple(), tuple(), self.staff(),
                         staff_policy="preserve")
        self.assertEqual(plan["operations"]["staff"], {"remove": [], "add": [], "update": []})

    def test_empty_staff_must_be_explicit_when_synchronizing(self) -> None:
        self.assertTrue(any("technische staf ontbreekt" in question
                            for question in preflight_questions(self.desired(), tuple())))
        self.assertEqual(preflight_questions(self.desired(), tuple(), allow_empty_staff=True), [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path, help="JSON with desired/current players and staff")
    parser.add_argument("--self-test", action="store_true", help="run offline safety tests")
    parser.add_argument("--confirm", action="store_true", help="ask for the exact match-specific confirmation token")
    args = parser.parse_args(argv)
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PlannerTests))
        return 0 if result.wasSuccessful() else 1
    if args.manifest is None:
        parser.error("manifest is required unless --self-test is used")
    try:
        matchsheet_id, desired, current, desired_staff, current_staff, rules, staff_policy, allow_empty_staff = load_manifest(args.manifest)
        print(render_preflight(matchsheet_id, desired, desired_staff, rules, staff_policy, allow_empty_staff))
        sys.stdout.flush()
        _require_clean_preflight(desired, desired_staff, rules, staff_policy, allow_empty_staff)
        if not args.confirm:
            print("Geen plan gemaakt: herhaal met --confirm na review.")
            return 4
        expected = f"BEVESTIG {matchsheet_id}"
        if input("Typ de getoonde bevestiging: ").strip() != expected:
            print("Geen plan gemaakt: bevestiging komt niet exact overeen.", file=sys.stderr)
            return 4
        print(json.dumps(make_plan(matchsheet_id, desired, current, desired_staff, current_staff,
                                   rules, staff_policy, allow_empty_staff), indent=2, sort_keys=True))
    except PreflightBlocked as exc:
        print(f"Geen plan gemaakt: {exc}", file=sys.stderr)
        return 3
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(f"Geen plan gemaakt: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
