#!/usr/bin/env python3
"""Apply a reviewed PSD selection as an e-Kickoff draft, never as submission.

This module is intentionally GUI-facing: display names arrive only from the
already-open PSD window and stay in memory.  The returned result contains only
opaque IDs and action kinds.  It supports an empty e-Kickoff players table;
populated sheets still require the separate explicit identity-mapping UI.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterable
import unittest

import psd_reader
import wedstrijdblad_adf_draft_adapter as adf
import wedstrijdblad_adf_staff_draft_adapter as staff_adf
import wedstrijdblad_adf_staff_reader as staff_reader
import wedstrijdblad_browser as browser
import wedstrijdblad_combined_sync as combined
import wedstrijdblad_draft_executor as executor
import wedstrijdblad_execution_guard as guard
import wedstrijdblad_populated_sync as populated
import wedstrijdblad_staff_sync as staff_sync


def _references(rows: Iterable[psd_reader.RawPlayerRow], desired: list[dict[str, Any]]) -> dict[str, adf.PlayerReference]:
    names = {psd_reader.opaque_member_id(row.display_name): row.display_name for row in rows}
    result: dict[str, adf.PlayerReference] = {}
    for player in desired:
        member_id = str(player["psd_member_id"])
        try:
            result[member_id] = adf.PlayerReference(member_id, names[member_id])
        except KeyError as exc:
            raise executor.DraftExecutionError("PSD-rij ontbreekt voor de goedgekeurde selectie") from exc
    return result


def player_references(rows: Iterable[psd_reader.RawPlayerRow], desired: list[dict[str, Any]]) -> dict[str, adf.PlayerReference]:
    """Public, in-memory-only player references for a combined draft."""
    return _references(rows, desired)


def _desired_players(manifest: dict[str, Any]) -> tuple[executor.KickoffPlayer, ...]:
    return tuple(
        executor.KickoffPlayer(
            str(player["psd_member_id"]), int(player["shirt_number"]), bool(player["captain"]), bool(player["goalkeeper"])
        )
        for player in manifest["desired"]
    )


def opaque_player_state(players: Iterable[executor.KickoffPlayer]) -> list[dict[str, Any]]:
    """Render only roster state needed by the execution gate, never names."""
    state = [
        {
            "member_id": player.kickoff_member_id,
            "shirt_number": player.shirt_number,
            "captain": player.captain,
            "goalkeeper": player.goalkeeper,
        }
        for player in players
    ]
    return sorted(state, key=lambda item: item["member_id"])


async def discover_current_roster(
    manifest: dict[str, Any], rows: Iterable[psd_reader.RawPlayerRow], profile_dir: Path, channel: str,
) -> tuple[adf.DiscoveredPlayer, ...]:
    """Open the selected sheet and return temporary rows for explicit mapping."""
    matchsheet_id = str(manifest["matchsheet_id"])
    if matchsheet_id in browser.PROTECTED_MATCHSHEET_IDS:
        raise executor.DraftExecutionError(f"wedstrijdblad {matchsheet_id} is hard beschermd")
    references = _references(rows, list(manifest["desired"]))
    async_playwright = browser._require_playwright()
    async with async_playwright() as playwright:
        context = await browser.launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(browser.KICKOFF_URL, wait_until="domcontentloaded")
            await browser._wait_for_authenticated_home(page, prompt_for_login=False)
            await browser._open_match(page, matchsheet_id)
            return await adf.AdfDraftAdapter(page, references).discover_players()
        finally:
            await context.close()


async def discover_current_staff(
    matchsheet_id: str, profile_dir: Path, channel: str,
) -> tuple[staff_reader.DiscoveredStaff, ...]:
    """Read current staff only for the visible explicit mapping dialog."""
    if matchsheet_id in browser.PROTECTED_MATCHSHEET_IDS:
        raise executor.DraftExecutionError(f"wedstrijdblad {matchsheet_id} is hard beschermd")
    async_playwright = browser._require_playwright()
    async with async_playwright() as playwright:
        context = await browser.launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(browser.KICKOFF_URL, wait_until="domcontentloaded")
            await browser._wait_for_authenticated_home(page, prompt_for_login=False)
            await browser._open_match(page, matchsheet_id)
            return await staff_reader.AdfStaffReader(page).discover_staff()
        finally:
            await context.close()


async def _fresh_snapshot(page: Any, adapter: adf.AdfDraftAdapter, matchsheet_id: str) -> dict[str, Any]:
    players = await adapter.read_players()
    players_empty = not players
    await browser._open_tab(page, "Staff")
    staff_empty = await browser._empty_selection(page, "staff")
    send_disabled = await browser.confirmation_send_disabled(page)
    return {
        "mode": "read-only", "matchsheet_id": matchsheet_id,
        "players_empty": players_empty, "staff_empty": staff_empty, "send_disabled": send_disabled,
        "player_state": opaque_player_state(players),
    }


async def _fresh_combined_snapshot(
    page: Any, player_adapter: adf.AdfDraftAdapter, staff_adapter: staff_adf.AdfStaffDraftAdapter, matchsheet_id: str,
) -> dict[str, Any]:
    players = await player_adapter.read_players()
    staff = await staff_adapter.read_staff()
    send_disabled = await browser.confirmation_send_disabled(page)
    return {
        "mode": "read-only", "matchsheet_id": matchsheet_id,
        "players_empty": not players, "staff_empty": not staff, "send_disabled": send_disabled,
        "player_state": opaque_player_state(players), "staff_state": combined.opaque_staff_state(staff),
    }


async def apply_empty_draft(
    manifest: dict[str, Any], rows: Iterable[psd_reader.RawPlayerRow], plan: dict[str, Any], reviewed_target: dict[str, Any],
    typed_confirmation: str, profile_dir: Path, channel: str,
) -> tuple[executor.DraftAction, ...]:
    """Reread, gate and apply a draft.  No official submission path exists."""
    matchsheet_id = str(manifest["matchsheet_id"])
    if matchsheet_id in browser.PROTECTED_MATCHSHEET_IDS:
        raise executor.DraftExecutionError(f"wedstrijdblad {matchsheet_id} is hard beschermd")
    expected = _desired_players(manifest)
    references = _references(rows, list(manifest["desired"]))
    review_gate = guard.create_review_gate(plan, reviewed_target)
    async_playwright = browser._require_playwright()
    async with async_playwright() as playwright:
        context = await browser.launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(browser.KICKOFF_URL, wait_until="domcontentloaded")
            await browser._wait_for_authenticated_home(page, prompt_for_login=False)
            await browser._open_match(page, matchsheet_id)
            adapter = adf.AdfDraftAdapter(page, references)
            current = await _fresh_snapshot(page, adapter, matchsheet_id)
            return await executor.execute_reviewed_actions(
                adapter, expected, plan=plan, current_target_snapshot=current, review_gate=review_gate,
                typed_confirmation=typed_confirmation,
            )
        finally:
            await context.close()


async def apply_populated_draft(
    matchsheet_id: str, sync: populated.PopulatedSync, plan: dict[str, Any], reviewed_target: dict[str, Any],
    typed_confirmation: str, profile_dir: Path, channel: str,
) -> tuple[executor.DraftAction, ...]:
    """Apply a user-mapped populated-sheet diff; still never submit officially."""
    if matchsheet_id in browser.PROTECTED_MATCHSHEET_IDS:
        raise executor.DraftExecutionError(f"wedstrijdblad {matchsheet_id} is hard beschermd")
    review_gate = guard.create_review_gate(plan, reviewed_target)
    async_playwright = browser._require_playwright()
    async with async_playwright() as playwright:
        context = await browser.launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(browser.KICKOFF_URL, wait_until="domcontentloaded")
            await browser._wait_for_authenticated_home(page, prompt_for_login=False)
            await browser._open_match(page, matchsheet_id)
            adapter = adf.AdfDraftAdapter(page, sync.references)
            current = await _fresh_snapshot(page, adapter, matchsheet_id)
            return await executor.execute_reviewed_actions(
                adapter, sync.desired, plan=plan, current_target_snapshot=current, review_gate=review_gate,
                typed_confirmation=typed_confirmation,
            )
        finally:
            await context.close()


async def apply_combined_draft(
    matchsheet_id: str,
    desired_players: tuple[executor.KickoffPlayer, ...],
    player_references: dict[str, adf.PlayerReference],
    desired_staff: staff_sync.StaffSync,
    plan: dict[str, Any],
    reviewed_target: dict[str, Any],
    typed_confirmation: str,
    profile_dir: Path,
    channel: str,
) -> tuple[str, ...]:
    """Apply an already reviewed player+staff concept, never Save or Send."""
    if matchsheet_id in browser.PROTECTED_MATCHSHEET_IDS:
        raise executor.DraftExecutionError(f"wedstrijdblad {matchsheet_id} is hard beschermd")
    review_gate = guard.create_review_gate(plan, reviewed_target)
    async_playwright = browser._require_playwright()
    async with async_playwright() as playwright:
        context = await browser.launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(browser.KICKOFF_URL, wait_until="domcontentloaded")
            await browser._wait_for_authenticated_home(page, prompt_for_login=False)
            await browser._open_match(page, matchsheet_id)
            player_adapter = adf.AdfDraftAdapter(page, player_references)
            staff_adapter = staff_adf.AdfStaffDraftAdapter(page, desired_staff.references)
            current = await _fresh_combined_snapshot(page, player_adapter, staff_adapter, matchsheet_id)
            guard.verify_execution_gate(review_gate, plan, current, typed_confirmation)
            return await combined.execute_combined_actions(
                player_adapter, desired_players, staff_adapter, desired_staff.desired,
            )
        finally:
            await context.close()


def apply_empty_draft_sync(*args: Any, **kwargs: Any) -> tuple[executor.DraftAction, ...]:
    """Thread-friendly entry point for the Tk wizard."""
    return asyncio.run(apply_empty_draft(*args, **kwargs))


def discover_current_roster_sync(*args: Any, **kwargs: Any) -> tuple[adf.DiscoveredPlayer, ...]:
    return asyncio.run(discover_current_roster(*args, **kwargs))


def discover_current_staff_sync(*args: Any, **kwargs: Any) -> tuple[staff_reader.DiscoveredStaff, ...]:
    return asyncio.run(discover_current_staff(*args, **kwargs))


class DraftApplyTests(unittest.TestCase):
    def test_references_are_derived_only_from_selected_psd_rows(self) -> None:
        row = psd_reader.RawPlayerRow("7", "Example Player", True, False)
        member_id = psd_reader.opaque_member_id(row.display_name)
        refs = _references((row,), [{"psd_member_id": member_id}])
        self.assertEqual(refs[member_id].kickoff_member_id, member_id)

    def test_missing_selected_psd_row_blocks_before_browser_launch(self) -> None:
        with self.assertRaisesRegex(executor.DraftExecutionError, "PSD-rij"):
            _references((), [{"psd_member_id": "PSD-missing"}])

    def test_opaque_player_state_is_sorted_and_excludes_display_data(self) -> None:
        state = opaque_player_state(
            (executor.KickoffPlayer("EK-2", None, False, True), executor.KickoffPlayer("EK-1", 8, True, False))
        )
        self.assertEqual(state[0], {"member_id": "EK-1", "shirt_number": 8, "captain": True, "goalkeeper": False})
        self.assertNotIn("name", str(state).casefold())


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DraftApplyTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
