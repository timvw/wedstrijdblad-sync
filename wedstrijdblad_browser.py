#!/usr/bin/env python3
"""Read-only Playwright reader for an e-Kickoff match sheet.

This is deliberately not a writer.  It opens a visible, user-owned Edge/Chrome
profile, lets the user sign in locally, and prints only a minimal match-sheet
snapshot.  It never reads credentials, cookies, player names or birthdates and it
does not contain an apply/save/send implementation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Any


KICKOFF_URL = "https://www.wedstrijdbladen.be/astm/faces/application.jsf"
PROTECTED_MATCHSHEET_IDS = frozenset({"4344375"})
UNSAFE_ACTION_WORDS = frozenset({"save", "send", "delete", "bewaren", "versturen", "verwijderen"})
UI_LABELS = {
    "Players": ("Players", "Spelers"),
    "Staff": ("Staff", "Staf"),
    "Confirmation": ("Confirmation", "Bevestiging"),
    "send": ("Send", "Versturen"),
}
# Consent is the sole non-navigation UI state the reader may touch.  Keep this
# list intentionally small and exact: a new or ambiguous cookie dialog must be
# shown to the user instead of being guessed at or force-clicked through.
COOKIE_POLICY_SELECTOR = "#cookie-policy"
COOKIE_CONSENT_BUTTON_LABELS = (
    "Ok",
    "Akkoord",
    "Accepteren",
    "Alles accepteren",
    "Accept",
    "Accept all",
)
CONSOLE_AUTH_ATTEMPTS = 60
GUI_AUTH_ATTEMPTS = 1_200
PLAYER_TABLE_ATTEMPTS = 20
PLAYER_TABLE_INTERVAL_MS = 250


class ReadOnlyViolation(RuntimeError):
    """A future code change tried to use a mutating control in the reader."""


class BrowserProfileInUseError(RuntimeError):
    """The dedicated, user-owned automation profile is already open."""


class BrowserUnavailableError(RuntimeError):
    """The required locally installed browser channel cannot be launched."""


def is_profile_in_use_error(detail: str) -> bool:
    """Recognise Chromium's stable lock diagnostic without parsing paths."""
    normalized = detail.casefold()
    return "opening in existing browser session" in normalized or "user data directory is already in use" in normalized


def is_browser_unavailable_error(detail: str) -> bool:
    normalized = detail.casefold()
    return "executable doesn't exist" in normalized or "executable does not exist" in normalized


async def launch_sync_browser(playwright: Any, profile_dir: Path, channel: str) -> Any:
    """Open exactly one dedicated profile with a recovery-oriented error."""
    try:
        return await playwright.chromium.launch_persistent_context(
            str(profile_dir), channel=channel, headless=False
        )
    except Exception as exc:
        if is_profile_in_use_error(str(exc)):
            raise BrowserProfileInUseError(
                "Het browserprofiel van Wedstrijdblad Sync is al open. "
                "Sluit alleen dat Chrome/Edge-venster volledig en probeer opnieuw."
            ) from exc
        if is_browser_unavailable_error(str(exc)):
            browser_name = "Microsoft Edge" if channel == "msedge" else "Google Chrome"
            raise BrowserUnavailableError(
                f"{browser_name} is niet beschikbaar. Installeer of herstel die browser en start de wizard opnieuw."
            ) from exc
        raise


def assert_read_only_action(description: str) -> None:
    """Guard every browser click in this module with a human-readable action label."""
    normalized = description.casefold()
    if any(word in normalized for word in UNSAFE_ACTION_WORDS):
        raise ReadOnlyViolation(f"read-only reader refuses action: {description}")


def default_profile_dir() -> Path:
    """A dedicated profile avoids attaching to a person's ordinary browser."""
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "Library/Application Support"))
    return base / "WedstrijdbladSync" / "browser-profile"


def default_browser_channel() -> str:
    """Use Chrome on macOS and Edge for the packaged Windows application."""
    return "chrome" if sys.platform == "darwin" else "msedge"


def _require_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright ontbreekt. Installeer eerst: python -m pip install -r requirements-wedstrijdblad-sync.txt"
        ) from exc
    return async_playwright


def authentication_state(url: str, visible_text: str) -> str | None:
    """Classify the two known reauthentication states without treating them as 'no matches'."""
    if "sso.rbfa.be" in url:
        return "sso"
    if "page expired" in visible_text.casefold():
        return "expired"
    return None


def is_authenticated_application_url(url: str, visible_text: str) -> bool:
    """Recognise the e-Kickoff app after SSO without depending on UI language."""
    return (
        url.startswith("https://www.wedstrijdbladen.be/astm/")
        and authentication_state(url, visible_text) is None
    )


def _matchsheet_id_pattern(matchsheet_id: str) -> re.Pattern[str]:
    """Match an ID outside other digits; ADF does not honour ``\\b`` here."""
    return re.compile(rf"(?<!\d){re.escape(matchsheet_id)}(?!\d)")


async def _wait_for_authenticated_home(page: Any, prompt_for_login: bool = True) -> None:
    """Wait for a visible manual SSO without ever reading credentials.

    A GUI has no console in which to ask for Enter.  In that case it leaves
    the browser visible for five minutes and simply waits for the ordinary
    e-Kickoff application URL. The short probes also avoid a PSD-like redirect race:
    Oracle can redirect to SSO just after the initial application URL loaded.
    """
    prompted = False
    # A GUI has no terminal Enter prompt. Give its visible, user-owned login
    # five minutes, but keep probing in short slices: RBFA can complete the
    # final redirect exactly when a single long Playwright wait would time out.
    attempts = GUI_AUTH_ATTEMPTS if not prompt_for_login else CONSOLE_AUTH_ATTEMPTS
    for _attempt in range(attempts):
        try:
            visible_text = await page.locator("body").inner_text(timeout=1_000)
        except Exception:
            visible_text = ""
        state = authentication_state(page.url, visible_text)
        if is_authenticated_application_url(page.url, visible_text):
            return
        if state in {"sso", "expired"}:
            if prompt_for_login and not prompted:
                if state == "sso":
                    print("Meld je aan in het zichtbare RBFA-venster. Wacht tot de pagina 'Matches' toont.")
                else:
                    print("De e-Kickoff-sessie is verlopen. Meld opnieuw aan in het zichtbare venster.")
                input("Druk hier pas Enter wanneer de pagina 'Matches' opnieuw toont: ")
                prompted = True
        await page.wait_for_timeout(250)
    raise RuntimeError("e-Kickoff startscherm werd niet bereikbaar; controleer de zichtbare RBFA-login")


async def _open_match(page: Any, matchsheet_id: str) -> None:
    if matchsheet_id in PROTECTED_MATCHSHEET_IDS:
        raise ReadOnlyViolation(f"wedstrijdblad {matchsheet_id} is hard beschermd")
    match_text = page.get_by_text(_matchsheet_id_pattern(matchsheet_id))
    try:
        await match_text.wait_for(timeout=30_000)
    except Exception as exc:
        raise RuntimeError(f"wedstrijdblad {matchsheet_id} verscheen niet in de wedstrijdenlijst") from exc
    if await match_text.count() != 1:
        raise RuntimeError(f"verwacht exact één wedstrijd voor {matchsheet_id}; gevonden: {await match_text.count()}")
    # Use the unique visible ID to locate its own row. ADF can render a
    # hidden/mobile duplicate table, so querying all ``tr`` elements is not
    # safe. Neither branch performs a write.
    row = match_text.locator("xpath=ancestor::tr[1]")
    if await row.count() == 1:
        link = row.get_by_role("link")
    elif await row.count() == 0:
        link = match_text.locator("xpath=ancestor-or-self::*[self::a or @role='link']")
    else:
        raise RuntimeError(f"wedstrijdblad {matchsheet_id} heeft geen eenduidige zichtbare rij")
    if await link.count() != 1:
        raise RuntimeError(f"geen eenduidige open-link voor wedstrijdblad {matchsheet_id}")
    assert_read_only_action("open match sheet")
    await link.click()
    await page.get_by_text(re.compile(rf"(?:Matchsheet|Wedstrijdblad):\s*{re.escape(matchsheet_id)}"), exact=False).wait_for(timeout=30_000)


async def _open_tab(page: Any, name: str) -> None:
    try:
        names = UI_LABELS[name]
    except KeyError as exc:
        raise RuntimeError(f"onbekende e-Kickoff-tab: {name}") from exc
    tabs = [page.get_by_role("tab", name=label, exact=True) for label in names]
    available = [tab for tab in tabs if await tab.count() == 1]
    if len(available) != 1:
        raise RuntimeError(f"e-Kickoff tab ontbreekt of is dubbel: {name}")
    await _dismiss_cookie_policy(page)
    tab = available[0]
    assert_read_only_action(f"open {name} tab")
    if await tab.get_attribute("aria-selected") != "true":
        await tab.click()
    # ADF starts a partial refresh even for a tab click. Waiting for the
    # selected state is more reliable than a fixed quarter second and avoids
    # snapshotting the previous tab's controls.
    for _ in range(PLAYER_TABLE_ATTEMPTS):
        if await tab.get_attribute("aria-selected") == "true":
            return
        await page.wait_for_timeout(PLAYER_TABLE_INTERVAL_MS)
    raise RuntimeError(f"e-Kickoff tab werd niet actief: {name}")


async def confirmation_send_disabled(page: Any) -> bool | None:
    """Read the stable Send state after Confirmation's ADF refresh.

    ``None`` remains a valid result when the site does not render a recognised
    Send control, but only after waiting for the tab body to settle. Every
    reader and writer snapshot shares this function so a review cannot become
    stale merely because one path observed the controls earlier.
    """
    await _open_tab(page, "Confirmation")
    for _ in range(PLAYER_TABLE_ATTEMPTS):
        controls = [page.get_by_role("button", name=label, exact=True) for label in UI_LABELS["send"]]
        available = [control for control in controls if await control.count() == 1]
        if len(available) == 1:
            return await available[0].is_disabled()
        if len(available) > 1:
            raise RuntimeError("e-Kickoff Send-control is dubbelzinnig")
        await page.wait_for_timeout(PLAYER_TABLE_INTERVAL_MS)
    return None


def is_safe_cookie_consent_label(label: str) -> bool:
    """Allow only an exact, reviewed cookie-consent button label."""
    return label in COOKIE_CONSENT_BUTTON_LABELS


async def _dismiss_cookie_policy(page: Any) -> None:
    """Dismiss one known cookie banner before a read-only navigation click.

    Cookie consent does not change a match sheet.  It is deliberately handled
    separately from the read-only-action guard because the handler never looks
    outside ``#cookie-policy``, uses exact reviewed labels and refuses a dialog
    with zero or multiple candidate actions.
    """
    policy = page.locator(COOKIE_POLICY_SELECTOR)
    count = await policy.count()
    if count == 0:
        return
    if count != 1:
        raise RuntimeError("cookiebeleid is niet eenduidig; sluit het zichtbaar handmatig")
    if not await policy.is_visible():
        return

    candidates = []
    for label in COOKIE_CONSENT_BUTTON_LABELS:
        button = policy.get_by_role("button", name=label, exact=True)
        if await button.count() == 1:
            candidates.append(button)
        elif await button.count() > 1:
            raise RuntimeError("cookiebeleid heeft een dubbelzinnige akkoordknop; sluit het handmatig")
    if len(candidates) != 1:
        raise RuntimeError("cookiebeleid heeft geen eenduidige akkoordknop; sluit het zichtbaar handmatig")

    await candidates[0].click()
    await page.wait_for_timeout(100)
    if await policy.is_visible():
        raise RuntimeError("cookiebeleid bleef zichtbaar na akkoord; sluit het handmatig")


async def _empty_selection(page: Any, kind: str) -> bool:
    if kind == "players":
        return await _wait_for_player_selection_state(page)
    return _is_empty_selection_text(kind, await page.locator("body").inner_text())


async def _wait_for_player_selection_state(page: Any) -> bool:
    """Wait for an explicit empty notice or a real player row.

    Oracle ADF replaces the initial player panel asynchronously.  Treating the
    brief panel without rows as an empty selection could make a draft proposal
    destructive, so an unrecognised state always stops instead.
    """
    for _attempt in range(PLAYER_TABLE_ATTEMPTS):
        if _is_empty_selection_text("players", await page.locator("body").inner_text()):
            return True
        rows = page.locator("tr")
        for index in range(await rows.count()):
            row = rows.nth(index)
            if (
                await row.locator("input[type=radio]").count() == 1
                and await row.locator("input[type=checkbox]").count() == 1
                and await row.locator("input[type=text], input[type=number]").count() == 1
            ):
                return False
        await page.wait_for_timeout(PLAYER_TABLE_INTERVAL_MS)
    raise RuntimeError("ADF-spelerstabel werd niet leesbaar; conceptvoorstel is geblokkeerd")


def _is_empty_selection_text(kind: str, visible_text: str) -> bool:
    """Recognise the live ADF empty-state wording without treating errors as empty.

    The Players table currently renders the grammatically unusual ``select'ed``.
    Keep that exact observed variant alongside the normal wording; an empty
    list is a safety gate for dry-run, so a missed variant must not be silently
    interpreted as a non-empty selection.
    """
    body = visible_text.casefold()
    known_empty_messages = {
        "players": (
            "there are currently no players select'ed",
            "there are currently no players selected",
            "no players selected",
            "er zijn momenteel geen spelers geselecteerd",
        ),
        "staff": (
            "there are currently no staff members selected",
            "no staff members selected",
            "no staff selected",
            "er zijn momenteel geen staf leden geselecteerd",
            "er zijn momenteel geen stafleden geselecteerd",
        ),
    }
    try:
        return any(message in body for message in known_empty_messages[kind])
    except KeyError as exc:
        raise ValueError(f"onbekend e-Kickoff selectietype: {kind}") from exc


async def read_matchsheet(
    matchsheet_id: str, profile_dir: Path, channel: str, *, prompt_for_login: bool = True
) -> dict[str, Any]:
    """Return only non-PII status fields; all browser actions are read-only navigation."""
    async_playwright = _require_playwright()
    async with async_playwright() as playwright:
        context = await launch_sync_browser(playwright, profile_dir, channel)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(KICKOFF_URL, wait_until="domcontentloaded")
            await _wait_for_authenticated_home(page, prompt_for_login=prompt_for_login)
            await _open_match(page, matchsheet_id)

            await _open_tab(page, "Players")
            players_empty = await _empty_selection(page, "players")

            await _open_tab(page, "Staff")
            staff_empty = await _empty_selection(page, "staff")

            send_disabled = await confirmation_send_disabled(page)
            return {
                "mode": "read-only",
                "matchsheet_id": matchsheet_id,
                "players_empty": players_empty,
                "staff_empty": staff_empty,
                "send_disabled": send_disabled,
            }
        finally:
            await context.close()


class BrowserReaderTests(unittest.TestCase):
    def test_protected_sheet_cannot_be_opened(self) -> None:
        self.assertIn("4344375", PROTECTED_MATCHSHEET_IDS)

    def test_write_labels_are_rejected(self) -> None:
        for label in ("Save players", "Send", "Bewaren", "Versturen", "Delete all players"):
            with self.assertRaises(ReadOnlyViolation):
                assert_read_only_action(label)

    def test_read_navigation_is_allowed(self) -> None:
        assert_read_only_action("open Players tab")

    def test_authentication_is_not_misreported_as_an_empty_match_list(self) -> None:
        self.assertEqual(authentication_state("https://sso.rbfa.be/", "Sign in"), "sso")
        self.assertEqual(authentication_state(KICKOFF_URL, "Page Expired"), "expired")
        self.assertIsNone(authentication_state(KICKOFF_URL, "Matches"))

    def test_authenticated_application_detection_does_not_depend_on_a_translated_heading(self) -> None:
        self.assertTrue(is_authenticated_application_url(KICKOFF_URL, "Matchs"))
        self.assertFalse(is_authenticated_application_url("https://sso.rbfa.be/", "Matchs"))
        self.assertFalse(is_authenticated_application_url(KICKOFF_URL, "Page Expired"))

    def test_live_empty_player_wording_is_recognised(self) -> None:
        self.assertTrue(
            _is_empty_selection_text(
                "players", "There are currently no players select'ed. Please add a player using one of the buttons above."
            )
        )
        self.assertTrue(_is_empty_selection_text("staff", "There are currently no staff members selected."))
        self.assertTrue(_is_empty_selection_text("players", "Er zijn momenteel geen spelers geselecteerd."))
        self.assertFalse(_is_empty_selection_text("players", "An Oracle error occurred."))

    def test_profile_lock_is_recognised_without_exposing_a_profile_path(self) -> None:
        self.assertTrue(is_profile_in_use_error("BrowserType.launch_persistent_context: Opening in existing browser session."))
        self.assertTrue(is_profile_in_use_error("user data directory is already in use"))
        self.assertFalse(is_profile_in_use_error("Unexpected page navigation"))

    def test_missing_browser_binary_is_recognised(self) -> None:
        self.assertTrue(is_browser_unavailable_error("Executable doesn't exist at C:/missing/browser.exe"))
        self.assertTrue(is_browser_unavailable_error("executable does not exist"))
        self.assertFalse(is_browser_unavailable_error("Opening in existing browser session"))

    def test_gui_login_wait_is_longer_than_console_login_wait(self) -> None:
        # This guards the retry-based login path: do not reintroduce one
        # monolithic wait that can time out during RBFA's final redirect.
        self.assertGreater(GUI_AUTH_ATTEMPTS, CONSOLE_AUTH_ATTEMPTS)

    def test_matchsheet_id_pattern_requires_numeric_boundaries(self) -> None:
        pattern = _matchsheet_id_pattern("4358637")
        self.assertIsNotNone(pattern.search("Wedstrijdblad: 4358637"))
        self.assertIsNone(pattern.search("43586370"))
        self.assertIsNone(pattern.search("14358637"))

    def test_only_reviewed_exact_cookie_consent_labels_are_allowed(self) -> None:
        self.assertTrue(is_safe_cookie_consent_label("Ok"))
        self.assertTrue(is_safe_cookie_consent_label("Akkoord"))
        self.assertTrue(is_safe_cookie_consent_label("Accept all"))
        self.assertFalse(is_safe_cookie_consent_label("Sluiten"))
        self.assertFalse(is_safe_cookie_consent_label("Save preferences"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matchsheet_id", nargs="?", help="e-Kickoff match-sheet id")
    parser.add_argument("--profile-dir", type=Path, default=default_profile_dir())
    parser.add_argument("--channel", default=default_browser_channel(), choices=("msedge", "chrome"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(BrowserReaderTests)
        )
        return 0 if result.wasSuccessful() else 1
    if not args.matchsheet_id:
        parser.error("matchsheet_id is required unless --self-test is used")
    try:
        args.profile_dir.mkdir(parents=True, exist_ok=True)
        print(json.dumps(asyncio.run(read_matchsheet(str(args.matchsheet_id), args.profile_dir, args.channel)), indent=2))
    except KeyboardInterrupt:
        print("e-Kickoff uitlezen geannuleerd; er is niets gewijzigd", file=sys.stderr)
        return 130
    except Exception as exc:
        # Includes Playwright timeout/navigation exceptions.  The reader has no
        # mutating actions, so a concise retryable error is more useful than a
        # framework traceback to a Windows end user.
        print(f"Geen wedstrijdblad gelezen: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
