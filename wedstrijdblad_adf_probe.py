#!/usr/bin/env python3
"""Read-only, PII-free Oracle ADF control-map probe for a known match sheet.

This is a maintainer diagnostic, not an end-user workflow.  It opens the same
read-only tabs as :mod:`wedstrijdblad_browser` and reports only whitelisted
control labels plus ADF control IDs.  It deliberately never returns values,
row text, player names, birth dates, cookies or form payloads.

The resulting JSON is useful evidence before a future writer is considered:
Oracle ADF can replace control IDs after an AJAX refresh, so labels in a
manual observation are insufficient implementation evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any

import wedstrijdblad_browser as browser


SAFE_BUTTON_LABELS = frozenset(
    {
        "this player",
        "multiple players",
        "select default player",
        "copy players previous game",
        "delete all players",
        "save",
        "deze speler",
        "meerdere spelers",
        "selectie kernspelers",
        "kopieer spelers vorige wedstrijd",
        "alle spelers verwijderen",
        "bewaren",
        "this staff",
        "search staff",
        "select default staff",
        "copy staff previous game",
        "delete all staff",
        "deze staf",
        "zoek technische staf",
        "selectie technische staf club",
        "kopieer technische staf vorige wedstrijd",
        "alle stafleden wissen",
        "send",
        "versturen",
    }
)
SAFE_FIELD_LABELS = frozenset({"player", "staff"})
SAFE_TAB_LABELS = frozenset({"match data", "players", "staff", "confirmation"})


def _normalise(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


def sanitise_control(raw: dict[str, Any]) -> dict[str, str] | None:
    """Keep only fixed UI labels and opaque DOM identifiers.

    Arbitrary ADF control text may be a player/staff member name.  It is never
    serialised.  An unknown label is therefore represented only by its tag and
    opaque control id, which is enough to notice a layout change.
    """
    tag = str(raw.get("tag", "")).casefold()
    # Live ADF action controls are ``<a role=button>`` children whose own
    # IDs are empty.  Their immediate presentation container has the stable
    # ADF ID (for example ``...:qckadd``); preserve that non-personal ID.
    control_id = str(raw.get("id", "") or raw.get("parent_id", ""))
    if not tag or not control_id:
        return None
    label = _normalise(str(raw.get("label", "")))
    kind = "button" if tag == "button" or str(raw.get("role", "")).casefold() == "button" else "field"
    allowed = SAFE_BUTTON_LABELS if kind == "button" else SAFE_FIELD_LABELS
    return {
        "kind": kind,
        "id": control_id,
        "label": label if label in allowed else "<other>",
    }


async def _tab_control_map(page: Any, tab: str) -> dict[str, Any]:
    await browser._open_tab(page, tab)
    raw_controls = await page.locator("button, [role=button], input, select, textarea").evaluate_all(
        """elements => elements.map(element => ({
          tag: element.tagName.toLowerCase(),
          id: element.id || '',
          parent_id: element.parentElement?.id || '',
          role: element.getAttribute('role') || '',
          label: element.getAttribute('aria-label') || element.innerText ||
                 element.labels?.[0]?.innerText || element.getAttribute('placeholder') || ''
        }))"""
    )
    controls = [safe for raw in raw_controls if (safe := sanitise_control(raw)) is not None]
    controls.sort(key=lambda control: (control["kind"], control["id"]))
    headings = await page.locator("th").all_inner_texts()
    # Table headers are structural labels.  Omit unexpected headers rather
    # than assuming they do not contain person data after an ADF layout change.
    known_headings = {
        "n°", "nr", "name", "naam", "birthdate", "geboortedatum", "cap", "gk",
        "remarks/observations", "aanmerkingen/observaties", "function", "functie",
    }
    visible_headings = [heading for heading in headings if _normalise(heading) in known_headings]
    return {"tab": tab, "controls": controls, "table_headings": visible_headings}


async def probe_matchsheet(matchsheet_id: str, profile_dir: Path, channel: str, *, prompt_for_login: bool = True) -> dict[str, Any]:
    """Open a match sheet and return a minimal, non-personal control map."""
    if matchsheet_id in browser.PROTECTED_MATCHSHEET_IDS:
        raise browser.ReadOnlyViolation(f"wedstrijdblad {matchsheet_id} is hard beschermd")
    async_playwright = browser._require_playwright()
    async with async_playwright() as playwright:
        context = await browser.launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(browser.KICKOFF_URL, wait_until="domcontentloaded")
            await browser._wait_for_authenticated_home(page, prompt_for_login=prompt_for_login)
            await browser._open_match(page, matchsheet_id)
            return {
                "mode": "read-only-adf-control-map",
                "matchsheet_id": matchsheet_id,
                "tabs": [await _tab_control_map(page, tab) for tab in ("Players", "Staff", "Confirmation")],
            }
        finally:
            await context.close()


class AdfProbeTests(unittest.TestCase):
    def test_known_button_is_preserved_but_unknown_text_is_redacted(self) -> None:
        self.assertEqual(
            sanitise_control({"tag": "button", "id": "pt1:b1", "label": "This player"}),
            {"kind": "button", "id": "pt1:b1", "label": "this player"},
        )
        self.assertEqual(
            sanitise_control({"tag": "a", "id": "", "parent_id": "pt1:qckadd", "role": "button", "label": "Deze speler"}),
            {"kind": "button", "id": "pt1:qckadd", "label": "deze speler"},
        )
        self.assertEqual(
            sanitise_control({"tag": "button", "id": "pt1:row:1", "label": "Private Player Name"}),
            {"kind": "button", "id": "pt1:row:1", "label": "<other>"},
        )

    def test_value_and_unknown_fields_are_not_serialised(self) -> None:
        result = sanitise_control({"tag": "input", "id": "pt1:i1", "label": "Player", "value": "Private Name"})
        self.assertEqual(result, {"kind": "field", "id": "pt1:i1", "label": "player"})
        self.assertNotIn("value", result or {})

    def test_protected_sheet_is_known_to_probe_guard(self) -> None:
        self.assertIn("4344375", browser.PROTECTED_MATCHSHEET_IDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matchsheet_id", nargs="?", help="e-Kickoff match-sheet id")
    parser.add_argument("--profile-dir", type=Path, default=browser.default_profile_dir())
    parser.add_argument("--channel", default=browser.default_browser_channel(), choices=("msedge", "chrome"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(AdfProbeTests)
        )
        return 0 if result.wasSuccessful() else 1
    if not args.matchsheet_id:
        parser.error("matchsheet_id is required unless --self-test is used")
    try:
        result = asyncio.run(probe_matchsheet(args.matchsheet_id, args.profile_dir, args.channel))
        print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        print("ADF-probe geannuleerd; er is niets gewijzigd", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Geen ADF-control-map gelezen: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
