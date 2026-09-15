#!/usr/bin/env python3
"""Read a PSD match selection into a local, pseudonymous manifest.

This is a deliberately read-only Playwright adapter.  It opens the visible PSD
login page when needed, opens one explicitly identified upcoming fixture and
reads the selection.  It never clicks PSD's save or send controls and never
stores a name, birth date, credential, cookie or token.  Player names are used
only in browser memory to derive deterministic opaque local identifiers.

PSD does not expose a reliable goalkeeper flag in the selection table.  The
reader therefore emits ``goalkeeper: null`` unless a later verified PSD field
is added; the planner will stop and ask the user instead of guessing.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime
import hashlib
import json
import os
import re
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import wedstrijdblad_browser as browser


PSD_DASHBOARD_URL = "https://vklinden.prosoccerdata.com/dashboard"
PSD_GAMES_URL = "https://vklinden.prosoccerdata.com/sportive/games"
PROTECTED_MATCHSHEET_IDS = frozenset({"4344375"})
UNSAFE_ACTION_WORDS = frozenset({"save", "send", "opslaan", "versturen", "verwijder", "delete"})
# Confirmed club mappings.  They are suggestions for a visible chooser, not
# an implicit instruction to synchronize staff.
STAFF_FUNCTION_SUGGESTIONS = {
    "trainer": "T1",
    "delegate": "Officiële team afgevaardigde",
}
# Observed in e-Kickoff's live staff-function menu on 2026-09-14.  Keep this
# strict list adjacent to the confirmed PSD defaults: a stale UI label must
# stop the proposal before any browser mutation, not be typed optimistically.
EKICKOFF_STAFF_FUNCTIONS = frozenset({
    "T1", "T2", "T3", "TVJO", "Keeperstrainer", "T4", "T5", "T6", "T7",
    "Officiële team afgevaardigde", "Terreincommissaris 1", "Terreincommissaris 2",
    "Terreincommissaris 3", "Tafelcommissaris", "Verzorger", "Dokter",
    "Materiaalverantwoordelijke",
})
# e-Kickoff localises the *visible* options in the staff dropdown. Keep
# persisted plans language-neutral by resolving every reviewed display choice
# to the established Dutch canonical label. A future staff writer can then
# select the live option through this same verified vocabulary rather than
# guessing from a translated UI string.
EKICKOFF_STAFF_FUNCTION_ALIASES = {
    "T1": frozenset({"T1"}),
    "T2": frozenset({"T2"}),
    "T3": frozenset({"T3"}),
    "TVJO": frozenset({"TVJO", "Head of Youth Development"}),
    "Keeperstrainer": frozenset({"Keeperstrainer", "Goalkeeper coach"}),
    "T4": frozenset({"T4"}),
    "T5": frozenset({"T5"}),
    "T6": frozenset({"T6"}),
    "T7": frozenset({"T7"}),
    "Officiële team afgevaardigde": frozenset({"Officiële team afgevaardigde", "Official team delegate"}),
    "Terreincommissaris 1": frozenset({"Terreincommissaris 1", "Field Commissioner 1"}),
    "Terreincommissaris 2": frozenset({"Terreincommissaris 2", "Field Commissioner 2"}),
    "Terreincommissaris 3": frozenset({"Terreincommissaris 3", "Field Commissioner 3"}),
    "Tafelcommissaris": frozenset({"Tafelcommissaris", "Table Commissioner"}),
    "Verzorger": frozenset({"Verzorger", "Physician"}),
    "Dokter": frozenset({"Dokter", "Doctor"}),
    "Materiaalverantwoordelijke": frozenset({"Materiaalverantwoordelijke", "Kit manager"}),
}


class ReadOnlyViolation(RuntimeError):
    """A future change tried to use a mutating PSD action."""


class SnapshotError(RuntimeError):
    """PSD could not be read without making an unsafe assumption."""


@dataclass(frozen=True)
class Fixture:
    matchsheet_id: str
    date: str
    opponent: str

    def validate(self) -> None:
        if self.matchsheet_id in PROTECTED_MATCHSHEET_IDS:
            raise ReadOnlyViolation(f"wedstrijdblad {self.matchsheet_id} is hard beschermd")
        if "betekom" in self.opponent.casefold():
            raise ReadOnlyViolation("KAC Betekom is hard beschermd; PSD-reader opent die wedstrijd niet")
        if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", self.date):
            raise SnapshotError("datum moet DD/MM/JJJJ zijn")
        if not self.opponent.strip():
            raise SnapshotError("tegenstander ontbreekt")


@dataclass(frozen=True)
class RawPlayerRow:
    """Transient DOM values.  ``display_name`` is never serialized."""

    shirt_number: str
    display_name: str
    starter_active: bool
    substitute_active: bool


@dataclass(frozen=True)
class RawStaffRow:
    """Transient PSD staff data; names are never serialized."""

    display_name: str
    source_function: str
    present: bool


def assert_read_only_action(description: str) -> None:
    normalized = description.casefold()
    if any(word in normalized for word in UNSAFE_ACTION_WORDS):
        raise ReadOnlyViolation(f"PSD reader weigert muterende actie: {description}")


def default_profile_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "Library/Application Support"))
    return base / "WedstrijdbladSync" / "psd-browser-profile"


def default_browser_channel() -> str:
    """Use the locally supported browser without making a novice choose it."""
    return "chrome" if sys.platform == "darwin" else "msedge"


def _require_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright ontbreekt. Installeer eerst: python -m pip install -r requirements-wedstrijdblad-sync.txt"
        ) from exc
    return async_playwright


def _normalise_name(value: str) -> str:
    value = re.sub(r"\s*\(Gemarkeerd als onbekend door speler\)\s*", "", value, flags=re.IGNORECASE)
    return " ".join(value.casefold().split())


def opaque_member_id(display_name: str) -> str:
    """A deterministic local identifier, without persisting the source name."""
    normalised = _normalise_name(display_name)
    if not normalised:
        raise SnapshotError("PSD-speler zonder leesbare naam; handmatige controle nodig")
    digest = hashlib.sha256(f"vk-linden-psd-v1:{normalised}".encode("utf-8")).hexdigest()
    return f"PSD-{digest[:24]}"


def opaque_staff_id(display_name: str) -> str:
    """Deterministic, local-only identifier in a namespace separate from players."""
    normalised = _normalise_name(display_name)
    if not normalised:
        raise SnapshotError("PSD-staflid zonder leesbare naam; handmatige controle nodig")
    digest = hashlib.sha256(f"vk-linden-psd-staff-v1:{normalised}".encode("utf-8")).hexdigest()
    return f"PSD-S-{digest[:24]}"


def selected_players(rows: Iterable[RawPlayerRow]) -> tuple[list[dict[str, Any]], list[str]]:
    """Convert PSD's active status buttons to planner data without role guesses."""
    players: list[dict[str, Any]] = []
    questions: list[str] = []
    seen_ids: set[str] = set()
    for row in rows:
        if row.starter_active and row.substitute_active:
            questions.append(f"PSD toont basis én wissel voor {opaque_member_id(row.display_name)}")
            continue
        if not row.starter_active and not row.substitute_active:
            continue
        try:
            shirt_number = int(row.shirt_number)
        except ValueError:
            questions.append(f"PSD-rugnummer ontbreekt of is ongeldig voor {opaque_member_id(row.display_name)}")
            continue
        member_id = opaque_member_id(row.display_name)
        if member_id in seen_ids:
            questions.append("PSD bevat twee geselecteerde spelers met dezelfde lokale identifier")
            continue
        seen_ids.add(member_id)
        players.append(
            {
                "psd_member_id": member_id,
                "shirt_number": shirt_number,
                "role": "starter" if row.starter_active else "substitute",
                "captain": False,
                "goalkeeper": None,
            }
        )
    return players, questions


def selected_staff(rows: Iterable[RawStaffRow]) -> tuple[list[dict[str, str]], list[str]]:
    """Return present PSD staff with opaque IDs; do not infer an e-Kickoff role.

    PSD's labels (for example ``Trainer``) are not necessarily the official
    e-Kickoff role labels (for example ``T1``).  The source label is returned
    only for temporary UI presentation.  A caller must use
    ``apply_staff_function_choices`` before it can request staff sync.
    """
    staff: list[dict[str, str]] = []
    questions: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not row.present:
            continue
        member_id = opaque_staff_id(row.display_name)
        if member_id in seen:
            questions.append("PSD bevat hetzelfde aanwezige staflid meer dan één keer")
            continue
        seen.add(member_id)
        source_function = " ".join(row.source_function.split())
        if not source_function:
            questions.append(f"PSD-functie ontbreekt voor {member_id}")
            continue
        staff.append({"psd_staff_id": member_id, "source_function": source_function})
    return staff, questions


def suggested_staff_function_choices(rows: Iterable[RawStaffRow]) -> dict[str, str]:
    """Return only confirmed default functions for a visible staff-role UI.

    The caller must still show every returned value and pass the user's final
    choices to :func:`apply_staff_function_choices`.  Unknown PSD roles are
    deliberately omitted so the UI can require a manual choice.
    """
    staff, questions = selected_staff(rows)
    if questions:
        raise SnapshotError("; ".join(questions))
    return {
        item["psd_staff_id"]: STAFF_FUNCTION_SUGGESTIONS[" ".join(item["source_function"].casefold().split())]
        for item in staff
        if " ".join(item["source_function"].casefold().split()) in STAFF_FUNCTION_SUGGESTIONS
    }


def canonical_staff_function(choice: str) -> str:
    """Turn one reviewed Dutch/English dropdown label into a stable role.

    This is deliberately an exact, whitespace-normalised match. It accepts
    no partial role names and never attempts a fuzzy translation.
    """
    normalised = " ".join(str(choice).split())
    if not normalised:
        raise SnapshotError("e-Kickoff-functie mag niet leeg zijn")
    for canonical, aliases in EKICKOFF_STAFF_FUNCTION_ALIASES.items():
        if normalised in aliases:
            return canonical
    raise SnapshotError("e-Kickoff-functie bestaat niet in de geverifieerde functielijst")


def apply_staff_function_choices(
    manifest: dict[str, Any], raw_staff: Iterable[RawStaffRow], choices: dict[str, str],
) -> dict[str, Any]:
    """Apply explicit e-Kickoff functions without ever persisting staff names."""
    source_staff, questions = selected_staff(raw_staff)
    if questions:
        raise SnapshotError("; ".join(questions))
    expected_ids = {item["psd_staff_id"] for item in source_staff}
    if set(choices) != expected_ids:
        raise SnapshotError("kies voor elk aanwezig PSD-staflid exact één e-Kickoff-functie")
    desired_staff: list[dict[str, str]] = []
    for item in source_staff:
        function = canonical_staff_function(choices[item["psd_staff_id"]])
        desired_staff.append({"psd_staff_id": item["psd_staff_id"], "function": function})
    resolved = json.loads(json.dumps(manifest))
    resolved["staff_policy"] = "synchronize"
    resolved["allow_empty_staff"] = not desired_staff
    resolved["desired_staff"] = desired_staff
    return resolved


def _selection_rules(starters: int, substitutes: int) -> dict[str, Any]:
    """Validate a competition rule before it becomes persisted manifest data.

    PSD can contain more than three substitutes.  That is not a malformed
    selection by itself: the permitted count is a match/competition setting,
    supplied deliberately by the user rather than hard-coded into the reader.
    """
    if type(starters) is not int or type(substitutes) is not int:
        raise SnapshotError("aantal basis- en wisselspelers moet een geheel getal zijn")
    if not 0 <= starters <= 25 or not 0 <= substitutes <= 25 or starters + substitutes == 0:
        raise SnapshotError("aantal basis- en wisselspelers moet samen tussen 1 en 50 liggen")
    return {
        "starters": starters,
        "substitutes": substitutes,
        "captain_required": True,
        "captain_must_start": True,
        "goalkeeper_required": True,
    }


def make_manifest(
    fixture: Fixture, rows: Iterable[RawPlayerRow], *, starters: int = 11, substitutes: int = 3
) -> dict[str, Any]:
    fixture.validate()
    desired, questions = selected_players(rows)
    return {
        "matchsheet_id": fixture.matchsheet_id,
        "selection_rules": _selection_rules(starters, substitutes),
        "staff_policy": "preserve",
        "allow_empty_staff": False,
        "desired": desired,
        "current": [],
        "desired_staff": [],
        "current_staff": [],
        "reader_warnings": questions
        + [
            "Kapitein en doelman zijn niet uit de PSD-selectietabel afgeleid; bevestig ze expliciet.",
            "Staf blijft behouden totdat PSD-stafsemantiek afzonderlijk is geverifieerd.",
        ],
    }


def apply_human_roles(manifest: dict[str, Any], captain_member_id: str, goalkeeper_member_id: str) -> dict[str, Any]:
    """Apply the two choices PSD's selection table cannot safely provide."""
    desired = manifest.get("desired")
    if not isinstance(desired, list):
        raise SnapshotError("ongeldig lokaal PSD-manifest")
    ids = {player.get("psd_member_id") for player in desired if isinstance(player, dict)}
    if captain_member_id not in ids or goalkeeper_member_id not in ids:
        raise SnapshotError("kapitein of doelman hoort niet bij de gelezen PSD-selectie")
    captain = next(player for player in desired if player["psd_member_id"] == captain_member_id)
    if captain["role"] != "starter":
        raise SnapshotError("kapitein moet een basisspeler zijn")
    resolved = json.loads(json.dumps(manifest))
    for player in resolved["desired"]:
        player["captain"] = player["psd_member_id"] == captain_member_id
        player["goalkeeper"] = player["psd_member_id"] == goalkeeper_member_id
    return resolved


def confirm_roles_interactively(rows: Iterable[RawPlayerRow], manifest: dict[str, Any], matchsheet_id: str) -> dict[str, Any]:
    """Show names only on-screen, request explicit choices, persist only opaque IDs."""
    selected = [row for row in rows if row.starter_active != row.substitute_active]
    desired_by_id = {player["psd_member_id"]: player for player in manifest["desired"]}
    choices: list[tuple[str, RawPlayerRow]] = []
    for row in selected:
        member_id = opaque_member_id(row.display_name)
        if member_id not in desired_by_id:
            continue
        choices.append((member_id, row))
    print("\nPSD-selectie (alleen tijdelijk getoond; namen worden niet opgeslagen):")
    for index, (_member_id, row) in enumerate(choices, start=1):
        role = "basis" if row.starter_active else "wissel"
        print(f"  {index}. #{row.shirt_number} {row.display_name} ({role})")
    if not choices:
        raise SnapshotError("PSD bevat geen geselecteerde basis- of wisselspelers")
    expected = f"BEVESTIG {matchsheet_id}"
    if input(f"Typ exact '{expected}' om deze selectie lokaal te gebruiken: ").strip() != expected:
        raise SnapshotError("selectie is niet bevestigd; er is geen manifest geschreven")

    def pick(label: str, starters_only: bool) -> str:
        valid = {
            str(index): member_id
            for index, (member_id, row) in enumerate(choices, start=1)
            if not starters_only or row.starter_active
        }
        while True:
            answer = input(f"Nummer van de {label}{' (basis)' if starters_only else ''}: ").strip()
            if answer in valid:
                return valid[answer]
            print("Ongeldige keuze. Kies een nummer uit de getoonde selectie.")

    return apply_human_roles(manifest, pick("kapitein", True), pick("doelman", False))


async def _wait_for_authenticated_psd(page: Any, prompt_for_login: bool) -> None:
    """The user performs any visible login; this program never sees credentials."""
    # PSD first returns the dashboard URL and can redirect to central-login a
    # moment later.  Do not make a one-time URL decision immediately after
    # ``goto``: that race previously skipped the login prompt and failed after
    # thirty seconds.
    dashboard = page.get_by_text("Aankomende wedstrijden", exact=True)
    for _attempt in range(60):
        try:
            await dashboard.wait_for(timeout=1_000)
            return
        except Exception:  # Only a bounded readiness probe; report the real error below.
            pass
        if "central-login" in page.url:
            if prompt_for_login:
                print(
                    "Meld je zichtbaar aan in PSD en kies, indien gevraagd, het profiel met toegang tot de selecties. "
                    "Druk hier pas Enter wanneer het PSD-dashboard open is."
                )
                input()
            else:
                # The windowed wizard has no console.  Leave the visible browser to
                # the user and wait for the ordinary PSD redirect instead.
                await page.wait_for_url(re.compile(r".*/dashboard(?:[?#].*)?$"), timeout=300_000)
            await dashboard.wait_for(timeout=30_000)
            return
        await page.wait_for_timeout(250)
    raise SnapshotError("PSD-dashboard werd niet bereikbaar; controleer de zichtbare login")


def _fixture_action_class(fixture_date: str, *, today: date | None = None) -> str:
    """Choose PSD's read-only source route from the explicitly supplied date."""
    source_date = datetime.strptime(fixture_date, "%d/%m/%Y").date()
    return "wedstrijd-verslag" if source_date < (today or date.today()) else "wedstrijd-voorbereiding"


async def _active_fixture_modal(page: Any) -> Any:
    """Return exactly one visible PSD fixture modal, never its hidden duplicate."""
    modals = page.locator(".modal")
    visible = [modals.nth(index) for index in range(await modals.count()) if await modals.nth(index).is_visible()]
    if len(visible) != 1:
        raise SnapshotError("PSD-wedstrijdvenster ontbreekt of is dubbel")
    return visible[0]


async def _open_fixture(page: Any, fixture: Fixture) -> None:
    fixture.validate()
    # The dashboard only contains a limited upcoming-fixture view.  The
    # complete Games overview contains both upcoming and historical sources,
    # which is essential when a previous selection is deliberately reused.
    if "/sportive/games" not in page.url:
        assert_read_only_action("open PSD wedstrijdenoverzicht")
        await page.goto(PSD_GAMES_URL, wait_until="domcontentloaded")
    row = page.locator("tr").filter(has_text=re.compile(re.escape(fixture.date))).filter(
        has_text=re.compile(re.escape(fixture.opponent), re.IGNORECASE)
    )
    try:
        await row.first.wait_for(timeout=30_000)
    except Exception as exc:
        raise SnapshotError(
            f"verwacht exact één PSD-wedstrijd voor {fixture.date} tegen {fixture.opponent}; gevonden: 0"
        ) from exc
    count = await row.count()
    if count != 1:
        raise SnapshotError(f"verwacht exact één PSD-wedstrijd voor {fixture.date} tegen {fixture.opponent}; gevonden: {count}")
    action_class = _fixture_action_class(fixture.date)
    button = row.locator(f"button.{action_class}")
    if await button.count() != 1:
        raise SnapshotError("PSD-wedstrijd heeft geen eenduidige read-only bronknop")
    assert_read_only_action("open PSD wedstrijdbron")
    await button.click()
    # An upcoming fixture opens ``Wedstrijdvoorbereiding``.  A deliberate
    # historical source opens ``Wedstrijdverslag maken`` instead, but both
    # expose the same read-only Selection -> Players table.
    try:
        await page.locator(".modal.fade.in").first.wait_for(state="visible", timeout=30_000)
    except Exception as exc:
        raise SnapshotError("PSD-wedstrijdvenster werd niet geopend") from exc
    await _active_fixture_modal(page)


async def _open_selection(page: Any) -> None:
    modal = await _active_fixture_modal(page)
    selection_tab = modal.get_by_text("Selectie", exact=True)
    try:
        await selection_tab.first.wait_for(timeout=30_000)
    except Exception as exc:
        raise SnapshotError("PSD-selectietab werd niet geladen") from exc
    if await selection_tab.count() != 1:
        raise SnapshotError("PSD-selectietab ontbreekt of is dubbel")
    assert_read_only_action("open PSD selectie")
    await selection_tab.click()
    # The selection view itself has a second tab group (Players/Staff).  PSD
    # may remember Staff as the last viewed subtab, so never assume that
    # opening the outer Selection view made the player table visible.
    players_tab = modal.get_by_text("Spelers", exact=True)
    try:
        await players_tab.first.wait_for(timeout=30_000)
    except Exception as exc:
        raise SnapshotError("PSD-spelerssubtab werd niet geladen") from exc
    if await players_tab.count() != 1:
        raise SnapshotError("PSD-spelerssubtab ontbreekt of is dubbel")
    assert_read_only_action("open PSD spelerssubtab")
    await players_tab.click()
    await modal.get_by_text("Basisspelers", exact=False).wait_for(timeout=30_000)


async def _raw_rows(page: Any) -> list[RawPlayerRow]:
    """Read the table's status icons/classes after PSD has completed its AJAX refresh."""
    modal = await _active_fixture_modal(page)
    payload = await modal.locator("tr").evaluate_all(
        """rows => rows.map(row => {
          const buttons = [...row.querySelectorAll('button')];
          const active = icon => buttons.some(button => {
            const hasIcon = button.classList.contains(icon) || button.innerHTML.includes(icon);
            return hasIcon && !button.classList.contains('transparant');
          });
          const selects = [...row.querySelectorAll('select')];
          const number = selects.length ? selects[0].value : '';
          const cells = [...row.querySelectorAll('td')];
          return {
            number,
            // The player cell has two visible labels: the first span inside
            // ``.col-md-11`` is the member name, while the following grey
            // span is PSD metadata (team/category). Reading the whole cell
            // made an e-Kickoff autocomplete lookup impossible to verify
            // exactly. Do not concatenate the labels or infer from their
            // text: a changed structure produces an empty/blocked row.
            name: cells.length > 1
              ? (cells[1].querySelector('.col-md-11 > span')?.innerText || '')
              : '',
            starter: active('fa-group'),
            substitute: active('fa-exchange')
          };
        }).filter(row => row.name.trim())"""
    )
    return [
        RawPlayerRow(str(row["number"]), str(row["name"]), bool(row["starter"]), bool(row["substitute"]))
        for row in payload
    ]


async def _open_staff_subtab(page: Any) -> None:
    modal = await _active_fixture_modal(page)
    staff_tab = modal.get_by_text("Staff", exact=True)
    try:
        await staff_tab.first.wait_for(timeout=30_000)
    except Exception as exc:
        raise SnapshotError("PSD-stafsubtab werd niet geladen") from exc
    if await staff_tab.count() != 1:
        raise SnapshotError("PSD-stafsubtab ontbreekt of is dubbel")
    assert_read_only_action("open PSD staff subtab")
    await staff_tab.click()
    await page.wait_for_timeout(250)


async def _raw_staff_rows(page: Any) -> list[RawStaffRow]:
    """Read PSD attendance + source role; no browser mutation is performed."""
    modal = await _active_fixture_modal(page)
    payload = await modal.locator("tr").evaluate_all(
        """rows => rows.map(row => {
          const cells = [...row.querySelectorAll('td')];
          const buttons = [...row.querySelectorAll('button')];
          const hasActiveCheck = buttons.some(button =>
            (button.classList.contains('fa-check') || button.innerHTML.includes('fa-check')) &&
            !button.classList.contains('transparant')
          );
          const selects = [...row.querySelectorAll('select')];
          return {
            name: cells.length ? cells[0].innerText : '',
            // An absent row has an attendance-reason select followed by its
            // role select. A present row has only the role select. Read the
            // visible option text, not PSD's internal numeric role ID.
            function: selects.length ? (selects[selects.length - 1].selectedOptions[0]?.textContent || '').trim() : '',
            present: hasActiveCheck && selects.length === 1
          };
        }).filter(row => row.name.trim())"""
    )
    return [RawStaffRow(str(row["name"]), str(row["function"]), bool(row["present"])) for row in payload]


async def read_psd_staff_snapshot(
    fixture: Fixture, profile_dir: Path, channel: str, *, prompt_for_login: bool = True,
) -> list[RawStaffRow]:
    """Read only the source staff tab for the wizard's explicit role chooser."""
    fixture.validate()
    async_playwright = _require_playwright()
    async with async_playwright() as playwright:
        context = await browser.launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(PSD_DASHBOARD_URL, wait_until="domcontentloaded")
            await _wait_for_authenticated_psd(page, prompt_for_login)
            await _open_fixture(page, fixture)
            await _open_selection(page)
            await _open_staff_subtab(page)
            return await _raw_staff_rows(page)
        finally:
            await context.close()


async def read_psd_snapshot(
    fixture: Fixture, profile_dir: Path, channel: str, *, starters: int = 11, substitutes: int = 3,
    prompt_for_login: bool = True,
) -> tuple[dict[str, Any], list[RawPlayerRow]]:
    fixture.validate()
    async_playwright = _require_playwright()
    async with async_playwright() as playwright:
        context = await browser.launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(PSD_DASHBOARD_URL, wait_until="domcontentloaded")
            await _wait_for_authenticated_psd(page, prompt_for_login)
            await _open_fixture(page, fixture)
            await _open_selection(page)
            rows = await _raw_rows(page)
            return make_manifest(fixture, rows, starters=starters, substitutes=substitutes), rows
        finally:
            await context.close()


class PsdReaderTests(unittest.TestCase):
    def fixture(self) -> Fixture:
        return Fixture("4358637", "20/09/2026", "Oud-heverlee Leuven")

    def test_protected_match_is_rejected_before_browser_start(self) -> None:
        with self.assertRaises(ReadOnlyViolation):
            Fixture("4344375", "12/09/2026", "KAC Betekom").validate()

    def test_previous_non_protected_fixture_can_supply_a_target_sheet(self) -> None:
        # The match-sheet ID is the e-Kickoff target; the PSD source may be a
        # deliberately chosen older fixture with its own date/opponent.
        Fixture("4358637", "06/09/2026", "VC Bertem-leefdaal").validate()

    def test_historical_report_title_is_an_allowed_read_only_source_view(self) -> None:
        self.assertRegex("Wedstrijdverslag maken : VK Linden - VC Bertem-leefdaal", r"Wedstrijd(?:voorbereiding|verslag maken)")

    def test_full_games_overview_is_used_for_historical_sources(self) -> None:
        self.assertEqual(PSD_GAMES_URL, "https://vklinden.prosoccerdata.com/sportive/games")

    def test_historical_source_uses_match_report_not_match_preparation(self) -> None:
        self.assertEqual(_fixture_action_class("06/09/2026", today=date(2026, 9, 14)), "wedstrijd-verslag")
        self.assertEqual(_fixture_action_class("20/09/2026", today=date(2026, 9, 14)), "wedstrijd-voorbereiding")

    def test_only_active_group_or_exchange_is_selected(self) -> None:
        players, questions = selected_players(
            [
                RawPlayerRow("1", "A Player", True, False),
                RawPlayerRow("2", "B Player", False, True),
                RawPlayerRow("3", "C Player", False, False),
            ]
        )
        self.assertEqual([player["role"] for player in players], ["starter", "substitute"])
        self.assertEqual(questions, [])

    def test_ambiguous_roles_are_blocking_warnings(self) -> None:
        players, questions = selected_players([RawPlayerRow("1", "A Player", True, True)])
        self.assertEqual(players, [])
        self.assertEqual(len(questions), 1)

    def test_manifest_never_serializes_source_name(self) -> None:
        manifest = make_manifest(self.fixture(), [RawPlayerRow("1", "Milan Collaers", True, False)])
        encoded = json.dumps(manifest)
        self.assertNotIn("Milan", encoded)
        self.assertEqual(manifest["desired"][0]["goalkeeper"], None)

    def test_selected_staff_uses_opaque_ids_and_excludes_absent_members(self) -> None:
        staff, questions = selected_staff(
            [RawStaffRow("Example Coach", "Trainer", True), RawStaffRow("Example Away", "Trainer", False)]
        )
        self.assertEqual(questions, [])
        self.assertEqual(staff, [{"psd_staff_id": opaque_staff_id("Example Coach"), "source_function": "Trainer"}])
        self.assertNotIn("Example", json.dumps(staff))

    def test_staff_sync_needs_explicit_kickoff_function_choices(self) -> None:
        manifest = make_manifest(self.fixture(), [RawPlayerRow("1", "A Player", True, False)])
        staff = [RawStaffRow("Example Coach", "Trainer", True)]
        staff_id = opaque_staff_id("Example Coach")
        with self.assertRaisesRegex(SnapshotError, "elk aanwezig"):
            apply_staff_function_choices(manifest, staff, {})
        resolved = apply_staff_function_choices(manifest, staff, {staff_id: "T1"})
        self.assertEqual(resolved["staff_policy"], "synchronize")
        self.assertEqual(resolved["desired_staff"], [{"psd_staff_id": staff_id, "function": "T1"}])

    def test_confirmed_staff_roles_are_suggestions_not_silent_sync(self) -> None:
        rows = [
            RawStaffRow("Example Coach", "Trainer", True),
            RawStaffRow("Example Delegate", " Delegate ", True),
            RawStaffRow("Example Other", "Kinesist", True),
        ]
        suggestions = suggested_staff_function_choices(rows)
        self.assertEqual(suggestions[opaque_staff_id("Example Coach")], "T1")
        self.assertEqual(suggestions[opaque_staff_id("Example Delegate")], "Officiële team afgevaardigde")
        self.assertNotIn(opaque_staff_id("Example Other"), suggestions)

    def test_english_staff_dropdown_label_resolves_to_canonical_function(self) -> None:
        manifest = make_manifest(self.fixture(), [RawPlayerRow("1", "A Player", True, False)])
        staff = [RawStaffRow("Example Delegate", "Delegate", True)]
        resolved = apply_staff_function_choices(
            manifest, staff, {opaque_staff_id("Example Delegate"): "Official team delegate"},
        )
        self.assertEqual(
            resolved["desired_staff"],
            [{"psd_staff_id": opaque_staff_id("Example Delegate"), "function": "Officiële team afgevaardigde"}],
        )

    def test_staff_function_must_be_in_the_verified_kickoff_menu(self) -> None:
        manifest = make_manifest(self.fixture(), [RawPlayerRow("1", "A Player", True, False)])
        staff = [RawStaffRow("Example Coach", "Trainer", True)]
        with self.assertRaisesRegex(SnapshotError, "geverifieerde functielijst"):
            apply_staff_function_choices(manifest, staff, {opaque_staff_id("Example Coach"): "Onbekende rol"})

    def test_more_than_three_substitutes_are_a_configurable_rule(self) -> None:
        manifest = make_manifest(
            self.fixture(), [RawPlayerRow(str(index), f"Player {index}", index <= 11, index > 11) for index in range(1, 17)],
            starters=11, substitutes=5,
        )
        self.assertEqual(manifest["selection_rules"]["substitutes"], 5)
        self.assertEqual(len(manifest["desired"]), 16)

    def test_human_roles_are_applied_only_to_known_selected_players(self) -> None:
        manifest = make_manifest(
            self.fixture(),
            [RawPlayerRow("1", "A Player", True, False), RawPlayerRow("2", "B Player", False, True)],
        )
        captain = manifest["desired"][0]["psd_member_id"]
        goalkeeper = manifest["desired"][1]["psd_member_id"]
        resolved = apply_human_roles(manifest, captain, goalkeeper)
        self.assertEqual(sum(player["captain"] for player in resolved["desired"]), 1)
        self.assertEqual(sum(player["goalkeeper"] for player in resolved["desired"]), 1)

    def test_substitute_captain_is_rejected(self) -> None:
        manifest = make_manifest(
            self.fixture(),
            [RawPlayerRow("1", "A Player", True, False), RawPlayerRow("2", "B Player", False, True)],
        )
        with self.assertRaisesRegex(SnapshotError, "basisspeler"):
            apply_human_roles(manifest, manifest["desired"][1]["psd_member_id"], manifest["desired"][0]["psd_member_id"])

    def test_mutating_action_labels_are_rejected(self) -> None:
        for label in ("Opslaan", "Selectie versturen", "delete speler"):
            with self.assertRaises(ReadOnlyViolation):
                assert_read_only_action(label)

    def test_player_subtab_navigation_is_read_only(self) -> None:
        assert_read_only_action("open PSD spelerssubtab")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matchsheet_id", nargs="?", help="e-Kickoff wedstrijdblad-id")
    parser.add_argument("--date", help="PSD-wedstrijddatum DD/MM/JJJJ")
    parser.add_argument("--opponent", help="PSD-tegenstander zoals zichtbaar in dashboard")
    parser.add_argument("--output", type=Path, help="lokaal manifestbestand (JSON)")
    parser.add_argument("--starters", type=int, default=11, help="vereist aantal basisspelers (standaard: 11)")
    parser.add_argument("--substitutes", type=int, default=3, help="vereist aantal wisselspelers (standaard: 3)")
    parser.add_argument("--profile-dir", type=Path, default=default_profile_dir())
    parser.add_argument("--channel", default=default_browser_channel(), choices=("msedge", "chrome"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(PsdReaderTests)
        )
        return 0 if result.wasSuccessful() else 1
    if not all((args.matchsheet_id, args.date, args.opponent, args.output)):
        parser.error("matchsheet_id, --date, --opponent en --output zijn vereist")
    fixture = Fixture(str(args.matchsheet_id), args.date, args.opponent)
    try:
        fixture.validate()
        manifest, rows = asyncio.run(
            read_psd_snapshot(fixture, args.profile_dir, args.channel, starters=args.starters, substitutes=args.substitutes)
        )
        manifest = confirm_roles_interactively(rows, manifest, fixture.matchsheet_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"PSD-selectie gelezen; lokaal manifest geschreven naar {args.output}")
        print(f"Geselecteerd: {len(manifest['desired'])}; kapitein en doelman zijn expliciet bevestigd.")
    except KeyboardInterrupt:
        print("PSD-selectie geannuleerd; er is geen manifest geschreven", file=sys.stderr)
        return 130
    except Exception as exc:
        # Playwright exposes navigation/timeout failures as its own exception
        # types.  At this executable boundary they are recoverable user states
        # (usually a visible login not yet completed), not a Python traceback.
        print(f"Geen PSD-selectie gelezen: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
