#!/usr/bin/env python3
"""Deterministic approval gate for a future e-Kickoff draft writer.

No browser code lives here.  This pure module binds a reviewed, PII-free plan
to the exact read-only target snapshot from which it was made.  A future
writer must re-read the sheet immediately before every mutation and pass the
same gate; otherwise it stops rather than applying a stale proposal.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from dataclasses import dataclass
from typing import Any


class ExecutionGateError(ValueError):
    """The reviewed plan, target state or explicit user approval is unsafe."""


FORBIDDEN_KEY_PARTS = frozenset({"name", "birth", "email", "password", "secret", "cookie", "token", "credential"})
SAFE_TARGET_KEYS = frozenset({"mode", "matchsheet_id", "players_empty", "staff_empty", "send_disabled", "player_state", "staff_state"})


def _safe_json(value: Any, path: str = "root") -> None:
    """Reject data that could turn a review artefact into a player-data log."""
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold().replace("_", "")
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise ExecutionGateError(f"onveilig veld in reviewgegevens: {path}.{key}")
            _safe_json(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _safe_json(item, f"{path}[{index}]")
    elif value is None or isinstance(value, (str, int, float, bool)):
        return
    else:
        raise ExecutionGateError(f"ongeldig reviewgegeven op {path}")


def _fingerprint(value: Any) -> str:
    _safe_json(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def safe_target_snapshot(target: dict[str, Any]) -> dict[str, Any]:
    """Keep only the proven, non-personal e-Kickoff state used by dry-run."""
    unknown = set(target) - SAFE_TARGET_KEYS
    if unknown:
        raise ExecutionGateError(f"doelsnapshot bevat niet-toegelaten velden: {', '.join(sorted(unknown))}")
    required = {"mode", "matchsheet_id", "players_empty", "staff_empty", "send_disabled", "player_state"}
    missing = required - set(target)
    if missing:
        raise ExecutionGateError(f"doelsnapshot mist velden: {', '.join(sorted(missing))}")
    snapshot = {key: target[key] for key in sorted(required)}
    # Bind every review to the exact opaque roster, not merely to
    # ``players_empty``. Names and dates never occur here: only a local opaque
    # id plus fields that determine the draft diff.
    state = target["player_state"]
    if not isinstance(state, list):
        raise ExecutionGateError("player_state moet een lijst zijn")
    normalized: list[dict[str, Any]] = []
    for index, player in enumerate(state):
        if not isinstance(player, dict) or set(player) != {"member_id", "shirt_number", "captain", "goalkeeper"}:
            raise ExecutionGateError(f"ongeldige player_state op index {index}")
        member_id = player["member_id"]
        number = player["shirt_number"]
        if not isinstance(member_id, str) or not member_id or type(player["captain"]) is not bool or type(player["goalkeeper"]) is not bool:
            raise ExecutionGateError(f"ongeldige player_state op index {index}")
        if number is not None and (type(number) is not int or not 1 <= number <= 99):
            raise ExecutionGateError(f"ongeldig rugnummer in player_state op index {index}")
        normalized.append({"member_id": member_id, "shirt_number": number, "captain": player["captain"], "goalkeeper": player["goalkeeper"]})
    if len({player["member_id"] for player in normalized}) != len(normalized):
        raise ExecutionGateError("dubbele opaque speler-ID in player_state")
    if bool(normalized) == bool(snapshot["players_empty"]):
        raise ExecutionGateError("player_state stemt niet overeen met players_empty")
    snapshot["player_state"] = sorted(normalized, key=lambda player: player["member_id"])
    # Staff identities are optional for legacy player-only dry-runs. A flow
    # that proposes staff actions must supply them, which binds the review to
    # the exact explicit-mapping source just as player_state does.
    if "staff_state" in target:
        staff_state = target["staff_state"]
        if not isinstance(staff_state, list):
            raise ExecutionGateError("staff_state moet een lijst zijn")
        normalized_staff: list[dict[str, str]] = []
        for index, staff in enumerate(staff_state):
            if not isinstance(staff, dict) or set(staff) != {"member_id", "function"}:
                raise ExecutionGateError(f"ongeldige staff_state op index {index}")
            member_id, function = staff["member_id"], staff["function"]
            if not isinstance(member_id, str) or not member_id or not isinstance(function, str):
                raise ExecutionGateError(f"ongeldige staff_state op index {index}")
            normalized_staff.append({"member_id": member_id, "function": function})
        if len({staff["member_id"] for staff in normalized_staff}) != len(normalized_staff):
            raise ExecutionGateError("dubbele opaque staf-ID in staff_state")
        if bool(normalized_staff) == bool(snapshot["staff_empty"]):
            raise ExecutionGateError("staff_state stemt niet overeen met staff_empty")
        snapshot["staff_state"] = sorted(normalized_staff, key=lambda staff: staff["member_id"])
    if snapshot["mode"] != "read-only" or not isinstance(snapshot["matchsheet_id"], str):
        raise ExecutionGateError("ongeldige read-only doelsnapshot")
    _safe_json(snapshot)
    return snapshot


@dataclass(frozen=True)
class ReviewGate:
    matchsheet_id: str
    target_fingerprint: str
    plan_fingerprint: str

    @property
    def confirmation(self) -> str:
        return f"PAS TOE {self.matchsheet_id} {self.plan_fingerprint[:10].upper()}"

    def as_mapping(self) -> dict[str, str]:
        return {
            "matchsheet_id": self.matchsheet_id,
            "target_fingerprint": self.target_fingerprint,
            "plan_fingerprint": self.plan_fingerprint,
            "required_confirmation": self.confirmation,
        }


def create_review_gate(plan: dict[str, Any], target: dict[str, Any]) -> ReviewGate:
    """Create a non-secret, deterministic gate after the user reviewed a plan."""
    snapshot = safe_target_snapshot(target)
    matchsheet_id = str(plan.get("matchsheet_id", ""))
    if not re.fullmatch(r"\d+", matchsheet_id) or matchsheet_id != snapshot["matchsheet_id"]:
        raise ExecutionGateError("plan en doelsnapshot verwijzen niet naar hetzelfde wedstrijdblad")
    return ReviewGate(matchsheet_id, _fingerprint(snapshot), _fingerprint(plan))


def verify_execution_gate(gate: ReviewGate, plan: dict[str, Any], current_target: dict[str, Any], typed_confirmation: str) -> None:
    """Reject stale state, plan edits and anything but exact user approval."""
    if create_review_gate(plan, current_target) != gate:
        raise ExecutionGateError("doelblad of plan wijzigde sinds de review; maak eerst een nieuw voorstel")
    if typed_confirmation.strip() != gate.confirmation:
        raise ExecutionGateError(f"bevestiging klopt niet; typ exact: {gate.confirmation}")


class ExecutionGateTests(unittest.TestCase):
    def target(self) -> dict[str, Any]:
        return {
            "mode": "read-only",
            "matchsheet_id": "4358637",
            "players_empty": True,
            "staff_empty": True,
            "send_disabled": True,
            "player_state": [],
        }

    def plan(self) -> dict[str, Any]:
        return {"matchsheet_id": "4358637", "operations": {"players": {"add": ["PSD-abc"]}}}

    def test_same_plan_and_snapshot_need_exact_confirmation(self) -> None:
        gate = create_review_gate(self.plan(), self.target())
        verify_execution_gate(gate, self.plan(), self.target(), gate.confirmation)

    def test_changed_target_is_stale(self) -> None:
        gate = create_review_gate(self.plan(), self.target())
        changed = self.target() | {
            "players_empty": False,
            "player_state": [{"member_id": "EK-new", "shirt_number": 4, "captain": False, "goalkeeper": False}],
        }
        with self.assertRaisesRegex(ExecutionGateError, "wijzigde sinds de review"):
            verify_execution_gate(gate, self.plan(), changed, gate.confirmation)

    def test_personal_field_is_rejected_before_fingerprinting(self) -> None:
        unsafe = self.plan() | {"player_name": "Niet opslaan"}
        with self.assertRaisesRegex(ExecutionGateError, "onveilig veld"):
            create_review_gate(unsafe, self.target())

    def test_unknown_target_data_is_rejected(self) -> None:
        unsafe = self.target() | {"player_name": "Niet opslaan"}
        with self.assertRaisesRegex(ExecutionGateError, "niet-toegelaten"):
            safe_target_snapshot(unsafe)

    def test_opaque_player_state_binds_a_populated_sheet_to_exact_roster(self) -> None:
        target = self.target() | {
            "players_empty": False,
            "player_state": [{"member_id": "EK-1", "shirt_number": 9, "captain": True, "goalkeeper": False}],
        }
        gate = create_review_gate(self.plan(), target)
        changed = target | {"player_state": [{"member_id": "EK-1", "shirt_number": 8, "captain": True, "goalkeeper": False}]}
        with self.assertRaisesRegex(ExecutionGateError, "wijzigde sinds de review"):
            verify_execution_gate(gate, self.plan(), changed, gate.confirmation)

    def test_missing_player_state_is_rejected(self) -> None:
        target = self.target()
        target.pop("player_state")
        with self.assertRaisesRegex(ExecutionGateError, "mist velden: player_state"):
            safe_target_snapshot(target)

    def test_optional_staff_state_binds_staff_changes(self) -> None:
        target = self.target() | {
            "staff_empty": False,
            "staff_state": [{"member_id": "EK-S-1", "function": "T1"}],
        }
        gate = create_review_gate(self.plan(), target)
        changed = target | {"staff_state": [{"member_id": "EK-S-1", "function": "T2"}]}
        with self.assertRaisesRegex(ExecutionGateError, "wijzigde sinds de review"):
            verify_execution_gate(gate, self.plan(), changed, gate.confirmation)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ExecutionGateTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
