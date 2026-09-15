#!/usr/bin/env python3
"""Deterministic, sequential draft executor for the constrained Oracle ADF adapter.

This module is intentionally browser-agnostic.  It turns a *user-confirmed*
identity-resolved roster into small sequential actions and requires a reread
after every action. It has no `Save` or `Send` action at all.

The concrete Oracle adapter is deliberately narrow and stops when the observed
ADF controls no longer match its reviewed selectors. It never exposes an
official submission action.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import wedstrijdblad_execution_guard as execution_guard


class DraftExecutionError(RuntimeError):
    """The live page diverged or an adapter action failed."""


MUTATING_ACTION_KINDS = frozenset({"remove", "add", "shirt_number", "goalkeeper", "captain"})


@dataclass(frozen=True)
class KickoffPlayer:
    """PII-free roster state exposed by a future adapter using stable opaque IDs."""

    kickoff_member_id: str
    shirt_number: int | None
    captain: bool
    goalkeeper: bool

    def __post_init__(self) -> None:
        if not self.kickoff_member_id:
            raise DraftExecutionError("lege e-Kickoff-speler-ID")
        if self.shirt_number is not None and (type(self.shirt_number) is not int or not 1 <= self.shirt_number <= 99):
            raise DraftExecutionError("ongeldig rugnummer in e-Kickoff-toestand")
        if type(self.captain) is not bool or type(self.goalkeeper) is not bool:
            raise DraftExecutionError("kapitein/doelman moet boolean zijn")


@dataclass(frozen=True)
class DraftAction:
    """One idempotence-checked mutation; no generic click or send operation exists."""

    kind: str
    member_id: str | None = None
    number: int | None = None


class DraftAdapter(Protocol):
    """Minimal API a verified Oracle ADF Playwright adapter must implement."""

    async def read_players(self) -> tuple[KickoffPlayer, ...]: ...

    async def add_player(self, kickoff_member_id: str) -> None: ...

    async def remove_player(self, kickoff_member_id: str) -> None: ...

    async def set_shirt_number(self, kickoff_member_id: str, shirt_number: int) -> None: ...

    async def set_goalkeeper(self, kickoff_member_id: str, goalkeeper: bool) -> None: ...

    async def set_captain(self, kickoff_member_id: str) -> None: ...

def _index(players: Sequence[KickoffPlayer]) -> dict[str, KickoffPlayer]:
    indexed = {player.kickoff_member_id: player for player in players}
    if len(indexed) != len(players):
        raise DraftExecutionError("e-Kickoff bevat dubbele stabiele speler-ID's; handmatige controle nodig")
    return indexed


def validate_desired(players: Sequence[KickoffPlayer]) -> tuple[KickoffPlayer, ...]:
    """Reject incomplete roster state before any browser action can start."""
    indexed = _index(players)
    if not indexed:
        raise DraftExecutionError("lege gewenste spelerslijst")
    if any(player.shirt_number is None for player in players):
        raise DraftExecutionError("elke gewenste speler heeft een rugnummer nodig")
    numbers = [player.shirt_number for player in players]
    if len(set(numbers)) != len(numbers):
        raise DraftExecutionError("dubbel rugnummer in gewenste selectie")
    if sum(player.captain for player in players) != 1:
        raise DraftExecutionError("exact één kapitein vereist")
    if sum(player.goalkeeper for player in players) != 1:
        raise DraftExecutionError("exact één doelman vereist")
    return tuple(sorted(players, key=lambda player: (player.shirt_number or 0, player.kickoff_member_id)))


def make_actions(desired: Sequence[KickoffPlayer], current: Sequence[KickoffPlayer]) -> tuple[DraftAction, ...]:
    """Create an ordered ADF-safe action list without parallelism.

    New players are added first, then *all* desired shirt numbers are written:
    ADF assigns new players an empty number field and can retain stale values
    for existing rows.  Goalkeeper and captain are set only after the roster is
    exact, so their radio/checkbox state cannot refer to a removed row.
    """
    desired = validate_desired(desired)
    desired_by_id = _index(desired)
    current_by_id = _index(current)
    actions: list[DraftAction] = []
    actions.extend(DraftAction("remove", member_id) for member_id in sorted(set(current_by_id) - set(desired_by_id)))
    actions.extend(
        DraftAction("add", player.kickoff_member_id)
        for player in desired
        if player.kickoff_member_id not in current_by_id
    )
    actions.extend(DraftAction("shirt_number", player.kickoff_member_id, player.shirt_number) for player in desired)
    actions.extend(DraftAction("goalkeeper", player.kickoff_member_id) for player in desired if player.goalkeeper)
    captain = next(player for player in desired if player.captain)
    actions.append(DraftAction("captain", captain.kickoff_member_id))
    return tuple(actions)


def require_known_action(action: DraftAction) -> None:
    """Keep the executor closed to generic browser clicks and official send."""
    if action.kind not in MUTATING_ACTION_KINDS:
        raise DraftExecutionError(f"onbekende draftactie: {action.kind}")


async def _require_present(adapter: DraftAdapter, member_id: str) -> KickoffPlayer:
    player = _index(await adapter.read_players()).get(member_id)
    if player is None:
        raise DraftExecutionError(f"speler {member_id} ontbreekt na ADF-herlezing")
    return player


async def execute_actions(adapter: DraftAdapter, desired: Sequence[KickoffPlayer]) -> tuple[DraftAction, ...]:
    """Execute and verify every concept action, never Save or Send.

    The caller is responsible for checking the review gate immediately before
    calling this function.  This function never sends/submits a match sheet.
    """
    desired = validate_desired(desired)
    actions = make_actions(desired, await adapter.read_players())
    for action in actions:
        require_known_action(action)
        if action.kind == "remove":
            assert action.member_id
            if action.member_id not in _index(await adapter.read_players()):
                raise DraftExecutionError(f"speler {action.member_id} veranderde vóór verwijderen; nieuw voorstel vereist")
            await adapter.remove_player(action.member_id)
            if action.member_id in _index(await adapter.read_players()):
                raise DraftExecutionError(f"ADF bevestigt verwijderen van {action.member_id} niet")
        elif action.kind == "add":
            assert action.member_id
            if action.member_id in _index(await adapter.read_players()):
                raise DraftExecutionError(f"speler {action.member_id} bestond al vóór toevoegen; nieuw voorstel vereist")
            await adapter.add_player(action.member_id)
            await _require_present(adapter, action.member_id)
        elif action.kind == "shirt_number":
            assert action.member_id and action.number is not None
            await _require_present(adapter, action.member_id)
            await adapter.set_shirt_number(action.member_id, action.number)
            if (await _require_present(adapter, action.member_id)).shirt_number != action.number:
                raise DraftExecutionError(f"ADF bevestigt rugnummer voor {action.member_id} niet")
        elif action.kind == "goalkeeper":
            assert action.member_id
            await _require_present(adapter, action.member_id)
            await adapter.set_goalkeeper(action.member_id, True)
            if not (await _require_present(adapter, action.member_id)).goalkeeper:
                raise DraftExecutionError(f"ADF bevestigt doelman voor {action.member_id} niet")
        elif action.kind == "captain":
            assert action.member_id
            await _require_present(adapter, action.member_id)
            await adapter.set_captain(action.member_id)
            captains = [player.kickoff_member_id for player in await adapter.read_players() if player.captain]
            if captains != [action.member_id]:
                raise DraftExecutionError("ADF bevestigt niet exact één kapitein")

    actual = _index(await adapter.read_players())
    expected = _index(desired)
    if actual != expected:
        raise DraftExecutionError("eindherlezing wijkt af van de goedgekeurde selectie")
    return actions


async def execute_reviewed_actions(
    adapter: DraftAdapter,
    desired: Sequence[KickoffPlayer],
    *,
    plan: dict[str, Any],
    current_target_snapshot: dict[str, Any],
    review_gate: execution_guard.ReviewGate,
    typed_confirmation: str,
) -> tuple[DraftAction, ...]:
    """Public entry point for a future writer, mandatory gate first.

    A concrete browser adapter must obtain ``current_target_snapshot`` by
    rereading the sheet immediately before this call.  The protocol cannot
    bypass the gate by accepting a generic `click` callback.
    """
    execution_guard.verify_execution_gate(review_gate, plan, current_target_snapshot, typed_confirmation)
    return await execute_actions(adapter, desired)


class FakeAdapter:
    """In-memory ADF model for protocol tests.  It has deliberately no send method."""

    def __init__(self, players: Sequence[KickoffPlayer] = ()) -> None:
        self.players = {player.kickoff_member_id: player for player in players}
        self.calls: list[str] = []

    async def read_players(self) -> tuple[KickoffPlayer, ...]:
        self.calls.append("read")
        return tuple(sorted(self.players.values(), key=lambda player: player.kickoff_member_id))

    async def add_player(self, member_id: str) -> None:
        self.calls.append(f"add:{member_id}")
        self.players[member_id] = KickoffPlayer(member_id, None, False, False)

    async def remove_player(self, member_id: str) -> None:
        self.calls.append(f"remove:{member_id}")
        self.players.pop(member_id, None)

    async def set_shirt_number(self, member_id: str, number: int) -> None:
        self.calls.append(f"number:{member_id}:{number}")
        player = self.players[member_id]
        self.players[member_id] = KickoffPlayer(member_id, number, player.captain, player.goalkeeper)

    async def set_goalkeeper(self, member_id: str, goalkeeper: bool) -> None:
        self.calls.append(f"goalkeeper:{member_id}:{goalkeeper}")
        for current_id, player in tuple(self.players.items()):
            self.players[current_id] = KickoffPlayer(current_id, player.shirt_number, player.captain, goalkeeper and current_id == member_id)

    async def set_captain(self, member_id: str) -> None:
        self.calls.append(f"captain:{member_id}")
        for current_id, player in tuple(self.players.items()):
            self.players[current_id] = KickoffPlayer(current_id, player.shirt_number, current_id == member_id, player.goalkeeper)

class DraftExecutorTests(unittest.TestCase):
    def desired(self) -> tuple[KickoffPlayer, ...]:
        return (
            KickoffPlayer("EK-1", 1, True, True),
            KickoffPlayer("EK-2", 9, False, False),
        )

    def test_actions_are_sequential_and_never_send(self) -> None:
        adapter = FakeAdapter((KickoffPlayer("EK-OLD", 7, False, False),))
        actions = asyncio.run(execute_actions(adapter, self.desired()))
        self.assertEqual([action.kind for action in actions], ["remove", "add", "add", "shirt_number", "shirt_number", "goalkeeper", "captain"])
        self.assertFalse(any("save" in action.kind for action in actions))
        self.assertFalse(any("send" in call for call in adapter.calls))
        self.assertGreater(adapter.calls.count("read"), len(actions))

    def test_protocol_has_no_save_or_submission_method(self) -> None:
        public = {name for name in dir(FakeAdapter) if not name.startswith("_")}
        self.assertNotIn("save_draft", public)
        self.assertNotIn("submit_official", public)

    def test_duplicate_number_blocks_before_adapter_action(self) -> None:
        invalid = (KickoffPlayer("EK-1", 1, True, True), KickoffPlayer("EK-2", 1, False, False))
        adapter = FakeAdapter()
        with self.assertRaisesRegex(DraftExecutionError, "dubbel rugnummer"):
            asyncio.run(execute_actions(adapter, invalid))
        self.assertEqual(adapter.calls, [])

    def test_unknown_action_is_rejected(self) -> None:
        with self.assertRaisesRegex(DraftExecutionError, "onbekende"):
            require_known_action(DraftAction("click_anything"))

    def test_stale_review_gate_blocks_before_adapter_action(self) -> None:
        plan = {"matchsheet_id": "4358637", "operations": {"players": {"add": ["EK-1", "EK-2"]}}}
        target = {
            "mode": "read-only", "matchsheet_id": "4358637", "players_empty": True,
            "staff_empty": True, "send_disabled": True,
            "player_state": [],
        }
        gate = execution_guard.create_review_gate(plan, target)
        adapter = FakeAdapter()
        with self.assertRaisesRegex(execution_guard.ExecutionGateError, "wijzigde sinds de review"):
            asyncio.run(
                execute_reviewed_actions(
                    adapter, self.desired(), plan=plan, current_target_snapshot=target | {
                        "players_empty": False,
                        "player_state": [{"member_id": "EK-new", "shirt_number": 4, "captain": False, "goalkeeper": False}],
                    },
                    review_gate=gate, typed_confirmation=gate.confirmation,
                )
            )
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DraftExecutorTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
