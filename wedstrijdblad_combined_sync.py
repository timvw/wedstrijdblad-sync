#!/usr/bin/env python3
"""Compose player and staff diffs into one PII-free review plan."""

from __future__ import annotations

from typing import Any, Iterable
import unittest

from wedstrijdblad_draft_executor import DraftAction, KickoffPlayer, execute_actions, make_actions
from wedstrijdblad_staff_executor import StaffDraftAdapter, execute_staff_actions
from wedstrijdblad_staff_sync import StaffSync, make_staff_actions


def opaque_staff_state(staff: Iterable[Any]) -> list[dict[str, str]]:
    """Render only opaque member/function fields for the review gate."""
    return sorted(
        ({"member_id": str(item.member_id), "function": str(item.function)} for item in staff),
        key=lambda item: item["member_id"],
    )


def _player_operations(actions: Iterable[DraftAction]) -> list[dict[str, Any]]:
    result = []
    for action in actions:
        item: dict[str, Any] = {"kind": f"player_{action.kind}"}
        if action.member_id:
            item["member_id"] = action.member_id
        if action.number is not None:
            item["number"] = action.number
        result.append(item)
    return result


def make_combined_plan(matchsheet_id: str, desired_players: Iterable[KickoffPlayer], current_players: Iterable[KickoffPlayer], staff_sync: StaffSync) -> dict[str, Any]:
    """Create one complete review artefact; it contains neither display names nor dates."""
    if not matchsheet_id.isdigit():
        raise ValueError("ongeldig wedstrijdblad-ID")
    operations = _player_operations(make_actions(tuple(desired_players), tuple(current_players)))
    operations.extend(make_staff_actions(staff_sync))
    return {"matchsheet_id": matchsheet_id, "operations": operations}


async def execute_combined_actions(
    player_adapter: Any, desired_players: Iterable[KickoffPlayer], staff_adapter: StaffDraftAdapter, desired_staff: Iterable[Any],
) -> tuple[str, ...]:
    """Run already-gated player then staff actions; neither executor saves."""
    players = await execute_actions(player_adapter, tuple(desired_players))
    staff = await execute_staff_actions(staff_adapter, tuple(desired_staff))
    return tuple(action.kind for action in players) + staff


class CombinedSyncTests(unittest.TestCase):
    def test_plan_keeps_staff_and_player_actions_opaque(self) -> None:
        from wedstrijdblad_staff_sync import KickoffStaff, StaffReference
        sync = StaffSync(
            (KickoffStaff("EK-S-1", "T1"),),
            (),
            {"EK-S-1": StaffReference("EK-S-1", "Example Staff")},
        )
        plan = make_combined_plan(
            "4358637",
            (KickoffPlayer("PSD-1", 1, True, True),),
            (),
            sync,
        )
        self.assertEqual([item["kind"] for item in plan["operations"]], ["player_add", "player_shirt_number", "player_goalkeeper", "player_captain", "staff_add", "staff_function"])
        self.assertNotIn("Example", str(plan))

    def test_combined_execution_never_adds_a_save_action(self) -> None:
        import asyncio
        from wedstrijdblad_draft_executor import FakeAdapter
        from wedstrijdblad_staff_executor import FakeStaffAdapter
        from wedstrijdblad_staff_sync import KickoffStaff

        players = (KickoffPlayer("EK-P-1", 1, True, True),)
        staff = (KickoffStaff("EK-S-1", "T1"),)
        player_adapter = FakeAdapter()
        staff_adapter = FakeStaffAdapter()
        actions = asyncio.run(execute_combined_actions(player_adapter, players, staff_adapter, staff))
        self.assertFalse(any("save" in action for action in actions))
        self.assertEqual(actions[-1], "staff_function")


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(CombinedSyncTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
