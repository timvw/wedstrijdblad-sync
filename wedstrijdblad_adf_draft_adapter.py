#!/usr/bin/env python3
"""Narrow Playwright adapter for an e-Kickoff *draft* player selection.

This is intentionally not a command-line program.  A GUI must first collect a
fresh PSD snapshot, exact identity choices and the typed review confirmation;
then it may create this adapter and pass it to ``wedstrijdblad_draft_executor``.

Display names exist only in the live browser session.  They are never returned,
printed, written to disk or included in review artefacts.  Every locator is
deliberately strict: an ADF layout change or an ambiguous autocomplete result
stops the draft rather than choosing a person heuristically.

The public adapter surface only supports additions, removals and player
properties. Saving or official submission is not represented by a method,
locator or generic click escape hatch.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Mapping
import unittest

from wedstrijdblad_draft_executor import DraftExecutionError, KickoffPlayer
import wedstrijdblad_browser as browser


class AdfDraftAdapterError(DraftExecutionError):
    """ADF did not expose the expected single control or row."""


@dataclass(frozen=True)
class PlayerReference:
    """Ephemeral exact identity choice made by the user in the mapping UI."""

    kickoff_member_id: str
    display_name: str
    birth_date: str | None = None

    def __post_init__(self) -> None:
        if not self.kickoff_member_id or not self.display_name.strip():
            raise AdfDraftAdapterError("lege tijdelijke spelersreferentie")


@dataclass(frozen=True)
class DiscoveredPlayer:
    """Ephemeral e-Kickoff row for the explicit mapping window.

    ``display_name`` and ``birth_date`` never leave the running process.  The
    `member_id` is a deterministic opaque handle used by the planner.
    """

    member_id: str
    display_name: str
    birth_date: str
    shirt_number: int | None
    captain: bool
    goalkeeper: bool


def opaque_kickoff_member_id(display_name: str, birth_date: str) -> str:
    """Create an in-memory opaque handle; never use a display value as an ID."""
    if not display_name.strip() or not birth_date.strip():
        raise AdfDraftAdapterError("e-Kickoff-speler mist naam of geboortedatum voor expliciete koppeling")
    raw = f"{display_name}\x00{birth_date}".encode("utf-8")
    return "EK-" + hashlib.sha256(raw).hexdigest()[:24]


@dataclass(frozen=True)
class PlayerLabels:
    """The two observed localisations; no hard-coded volatile ADF component IDs."""

    players_tabs: tuple[str, ...] = ("Spelers", "Players")
    player_fields: tuple[str, ...] = ("Speler", "Player")
    add_buttons: tuple[str, ...] = ("Deze speler", "This player")


AUTOCOMPLETE_SETTLE_ATTEMPTS = 20
AUTOCOMPLETE_SETTLE_INTERVAL_MS = 250
AUTOCOMPLETE_KEY_DELAY_MS = 25
ADF_ACTION_SETTLE_ATTEMPTS = 40
# Verified on the live e-Kickoff player row: column 2 is the shirt-number
# field, followed by name (3) and birth date (4).  Keep indices named so a
# future ADF layout change is reviewed deliberately.
PLAYER_ROW_NAME_COLUMN = 3
PLAYER_ROW_BIRTH_DATE_COLUMN = 4


def is_exact_autocomplete_match(display_name: str, visible_option: str) -> bool:
    """Match the exact name portion of an ADF suggestion, never a substring."""
    option = " ".join(visible_option.split())
    return option == display_name or option.startswith(f"{display_name} (")


def exact_autocomplete_options(display_name: str, visible_options: list[tuple[Any, str]]) -> list[Any]:
    """Return only exact name-prefix options from a transient ADF popup."""
    return [option for option, visible_text in visible_options if is_exact_autocomplete_match(display_name, visible_text)]


def is_real_player_row(radio_count: int, goalkeeper_count: int, number_count: int, cell_count: int) -> bool:
    """Recognise a compact player row, not ADF's outer control container.

    With exactly one child row left, ADF's enclosing row can also expose one
    Cap radio and one GK checkbox. Its many cells and multiple text fields
    prove that it is merely a container. A compact row with a changed control
    layout remains a blocking error rather than being skipped.
    """
    if radio_count != 1 or goalkeeper_count != 1:
        return False
    if number_count != 1:
        if cell_count > 10:
            return False
        raise AdfDraftAdapterError("ADF-spelersrij veranderde; identiteitskoppeling stopgezet")
    if cell_count < 6:
        raise AdfDraftAdapterError("ADF-spelersrij veranderde; identiteitskoppeling stopgezet")
    return True


def adf_id_locator(component_id: str) -> str:
    """Return a CSS attribute selector for a reviewed Oracle ADF component ID.

    ADF component IDs use colons (for example ``MR1:0:...``).  Those are not
    valid unescaped characters in a ``#id`` CSS selector, whereas an attribute
    selector treats them literally.  Keep the accepted alphabet narrow so a
    changed/unexpected DOM value cannot be interpolated as CSS.
    """
    if not re.fullmatch(r"[A-Za-z0-9:_-]+", component_id):
        raise AdfDraftAdapterError("ADF-autocompletepopup-ID heeft een onverwacht formaat")
    return f'[id="{component_id}"]'


def is_row_delete_icon(image_src: str | None) -> bool:
    """Recognise the observed ADF per-player delete icon without guessing."""
    return bool(image_src and image_src.rsplit("/", 1)[-1] == "DeleteTrash.png")


class AdfDraftAdapter:
    """Strict live adapter implementing the ``DraftAdapter`` protocol.

    ``references`` is the exact one-to-one mapping from an in-memory user
    choice UI.  It is purposely not serialised by this module.  To avoid
    accidental destructive use, the adapter refuses an unknown existing row;
    adding to an empty sheet is therefore supported immediately, while a
    populated sheet first needs complete identity resolution.
    """

    def __init__(self, page: Any, references: Mapping[str, PlayerReference], labels: PlayerLabels = PlayerLabels()) -> None:
        if not references:
            raise AdfDraftAdapterError("geen expliciete e-Kickoff-identiteitskoppelingen")
        if set(references) != {reference.kickoff_member_id for reference in references.values()}:
            raise AdfDraftAdapterError("ongeldige e-Kickoff-identiteitskoppelingen")
        if len({reference.display_name.casefold() for reference in references.values()}) != len(references):
            raise AdfDraftAdapterError("dubbele zichtbare spelersnaam; handmatige resolutie vereist")
        self._page = page
        self._references = dict(references)
        self._labels = labels

    async def _one_from_names(self, role: str, names: tuple[str, ...]) -> Any:
        candidates: list[Any] = []
        for name in names:
            locator = self._page.get_by_role(role, name=name, exact=True)
            if await locator.count() == 1:
                candidates.append(locator)
            elif await locator.count() > 1:
                raise AdfDraftAdapterError(f"ADF toont meer dan één {role}-control")
        if len(candidates) != 1:
            raise AdfDraftAdapterError(f"ADF-{role}-control ontbreekt of is niet eenduidig")
        return candidates[0]

    async def _open_players(self) -> None:
        # The same strictly scoped cookie-consent handler used by the reader
        # keeps a first-run browser profile from blocking a reviewed draft
        # action. It never clicks anywhere outside the recognised banner.
        await browser._dismiss_cookie_policy(self._page)
        tab = await self._one_from_names("tab", self._labels.players_tabs)
        # Clicking an already active ADF tab starts an unnecessary partial
        # refresh and can briefly leave two nested table containers in the
        # DOM. Reuse the selected tab so every mutation begins from the same
        # settled player table the preceding reread verified.
        if await tab.get_attribute("aria-selected") != "true":
            await tab.click()
        # Oracle ADF replaces partial-page controls after tab navigation. Do
        # not mistake its transient, row-less panel for an empty selection.
        await browser._wait_for_player_selection_state(self._page)

    def _reference(self, member_id: str) -> PlayerReference:
        try:
            return self._references[member_id]
        except KeyError as exc:
            raise AdfDraftAdapterError(f"onbekende opaque e-Kickoff-ID: {member_id}") from exc

    async def _row(self, member_id: str, *, required: bool = True) -> Any | None:
        reference = self._reference(member_id)
        # Oracle ADF can leave a hidden/mobile duplicate table in the DOM.
        # It must never turn an otherwise exact identity into an arbitrary
        # first-row choice, so identity operations consider rendered rows only.
        rows = self._page.locator("tr:visible").filter(has_text=reference.display_name)
        if reference.birth_date:
            rows = rows.filter(has_text=reference.birth_date)
        player_rows: list[Any] = []
        for index in range(await rows.count()):
            candidate = rows.nth(index)
            radio_count = await candidate.locator("input[type=radio]").count()
            goalkeeper_count = await candidate.locator("input[type=checkbox]").count()
            number_count = await candidate.locator("input[type=text], input[type=number]").count()
            if is_real_player_row(radio_count, goalkeeper_count, number_count, await candidate.locator("td").count()):
                player_rows.append(candidate)
        if not player_rows and not required:
            return None
        if len(player_rows) != 1:
            raise AdfDraftAdapterError(f"ADF-spelersrij voor {member_id} is niet eenduidig")
        row = player_rows[0]
        # ``has_text`` is necessarily a substring query.  Reject a partial
        # match rather than silently operating on an equally named player.
        row_text = " ".join((await row.inner_text()).split())
        if reference.display_name not in row_text:
            raise AdfDraftAdapterError(f"ADF-spelersrij voor {member_id} is niet exact controleerbaar")
        return row

    async def _role_controls(self, row: Any, member_id: str) -> tuple[Any, Any]:
        """Return the verified ADF Cap radio and GK checkbox controls."""
        captain = row.locator("input[type=radio]")
        goalkeeper = row.locator("input[type=checkbox]")
        if await captain.count() != 1 or await goalkeeper.count() != 1:
            raise AdfDraftAdapterError(f"ADF Cap/GK-controls voor {member_id} veranderden; stop veilig")
        return captain.first, goalkeeper.first

    async def _wait_for_row_presence(self, member_id: str, present: bool) -> Any | None:
        """Wait for an exact row to appear/disappear after an ADF mutation."""
        for _ in range(ADF_ACTION_SETTLE_ATTEMPTS):
            try:
                row = await self._row(member_id, required=False)
            except AdfDraftAdapterError:
                # ADF may briefly expose only an outer container while its
                # partial refresh replaces a player row.
                row = None
            if (row is not None) == present:
                return row
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        state = "verschijnen" if present else "verdwijnen"
        raise AdfDraftAdapterError(f"ADF-spelersrij voor {member_id} bleef niet {state}")

    async def _wait_for_shirt_number(self, member_id: str, shirt_number: int) -> None:
        for _ in range(ADF_ACTION_SETTLE_ATTEMPTS):
            try:
                row = await self._row(member_id, required=False)
                if row is not None:
                    field = row.locator("input[type=text], input[type=number]")
                    if await field.count() == 1 and (await field.first.input_value()).strip() == str(shirt_number):
                        return
            except AdfDraftAdapterError:
                pass
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        raise AdfDraftAdapterError(f"ADF-rugnummer voor {member_id} werd niet bevestigd")

    async def _wait_for_role(self, member_id: str, role: str) -> None:
        if role not in {"captain", "goalkeeper"}:
            raise AdfDraftAdapterError("onbekende ADF-spelersrol")
        for _ in range(ADF_ACTION_SETTLE_ATTEMPTS):
            try:
                row = await self._row(member_id, required=False)
                if row is not None:
                    captain, goalkeeper = await self._role_controls(row, member_id)
                    control = captain if role == "captain" else goalkeeper
                    if await control.is_checked():
                        return
            except AdfDraftAdapterError:
                pass
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        label = "kapitein" if role == "captain" else "doelman"
        raise AdfDraftAdapterError(f"ADF-{label} voor {member_id} werd niet bevestigd")

    async def read_players(self) -> tuple[KickoffPlayer, ...]:
        await self._open_players()
        players: list[KickoffPlayer] = []
        for member_id in sorted(self._references):
            row = await self._row(member_id, required=False)
            if row is None:
                continue
            shirt_fields = row.locator("input[type=text], input[type=number]")
            if await shirt_fields.count() != 1:
                raise AdfDraftAdapterError(f"ADF-rugnummercontrol voor {member_id} veranderde; stop veilig")
            raw_number = (await shirt_fields.first.input_value()).strip()
            try:
                shirt_number = int(raw_number) if raw_number else None
            except ValueError as exc:
                raise AdfDraftAdapterError(f"ADF-rugnummer voor {member_id} is geen geheel getal") from exc
            captain_box, goalkeeper_box = await self._role_controls(row, member_id)
            players.append(
                KickoffPlayer(member_id, shirt_number, await captain_box.is_checked(), await goalkeeper_box.is_checked())
            )
        # A row not represented in the reviewed identity map could be removed
        # by a set-diff.  Do not guess: the GUI must resolve it first.
        table_rows = self._page.locator("tr:visible")
        player_row_count = 0
        for index in range(await table_rows.count()):
            candidate = table_rows.nth(index)
            radio_count = await candidate.locator("input[type=radio]").count()
            checkbox_count = await candidate.locator("input[type=checkbox]").count()
            number_count = await candidate.locator("input[type=text], input[type=number]").count()
            if is_real_player_row(radio_count, checkbox_count, number_count, await candidate.locator("td").count()):
                player_row_count += 1
        if player_row_count != len(players):
            raise AdfDraftAdapterError("onbekende of ambigue speler op doelblad; eerst identiteiten koppelen")
        return tuple(players)

    async def discover_players(self) -> tuple[DiscoveredPlayer, ...]:
        """Read current rows for a human identity-mapping UI only.

        Names are shown only in the in-memory mapping window and are never
        printed or serialised. Its caller must turn selected rows into
        ``PlayerReference`` values in memory. This is intentionally separate
        from ``read_players`` so a writer can never silently invent a mapping
        for an existing player.
        """
        await self._open_players()
        result: list[DiscoveredPlayer] = []
        rows = self._page.locator("tr:visible")
        for index in range(await rows.count()):
            row = rows.nth(index)
            radio_count = await row.locator("input[type=radio]").count()
            goalkeeper_count = await row.locator("input[type=checkbox]").count()
            if radio_count != 1 or goalkeeper_count != 1:
                continue
            fields = row.locator("input[type=text], input[type=number]")
            cells = row.locator("td")
            if not is_real_player_row(radio_count, goalkeeper_count, await fields.count(), await cells.count()):
                continue
            display_name = " ".join((await cells.nth(PLAYER_ROW_NAME_COLUMN).inner_text()).split())
            birth_date = " ".join((await cells.nth(PLAYER_ROW_BIRTH_DATE_COLUMN).inner_text()).split())
            raw_number = (await fields.first.input_value()).strip()
            try:
                number = int(raw_number) if raw_number else None
            except ValueError as exc:
                raise AdfDraftAdapterError("ADF-rugnummer is geen geheel getal") from exc
            captain, goalkeeper = await self._role_controls(row, opaque_kickoff_member_id(display_name, birth_date))
            result.append(
                DiscoveredPlayer(
                    opaque_kickoff_member_id(display_name, birth_date), display_name, birth_date, number,
                    await captain.is_checked(), await goalkeeper.is_checked(),
                )
            )
        ids = [player.member_id for player in result]
        if len(ids) != len(set(ids)):
            raise AdfDraftAdapterError("dubbele e-Kickoff-identiteit; handmatige controle vereist")
        return tuple(result)

    async def add_player(self, kickoff_member_id: str) -> None:
        await self._open_players()
        reference = self._reference(kickoff_member_id)
        field = await self._one_from_names("combobox", self._labels.player_fields)
        # Oracle ADF's autosuggest listener is bound to keyboard events;
        # Playwright's ``fill`` updates the field but does not reliably open
        # the popup. Clear atomically, then type through the same event path a
        # user takes. This only changes the unsubmitted lookup field.
        await field.click()
        await field.fill("")
        await field.press_sequentially(reference.display_name, delay=AUTOCOMPLETE_KEY_DELAY_MS)
        popup_id = await field.get_attribute("aria-owns")
        if not popup_id:
            raise AdfDraftAdapterError("ADF-autocompletepopup ontbreekt")
        popup = self._page.locator(adf_id_locator(popup_id))
        await popup.wait_for(state="visible", timeout=10_000)
        # ADF exposes the result as a listbox whose visible option also
        # contains a birth date. The popup itself becomes visible *before*
        # its AJAX result arrives (and can briefly say "no results"). Poll
        # for a stable exact match rather than treating that transient state
        # as an identity failure.
        options = popup.locator("[role=option], li, a")
        candidates: list[Any] = []
        for _ in range(AUTOCOMPLETE_SETTLE_ATTEMPTS):
            visible_options: list[tuple[Any, str]] = []
            for index in range(await options.count()):
                option = options.nth(index)
                visible_options.append((option, " ".join((await option.inner_text()).split())))
            candidates = exact_autocomplete_options(reference.display_name, visible_options)
            if len(candidates) == 1:
                break
            if len(candidates) > 1:
                raise AdfDraftAdapterError(f"ADF-autocomplete voor {kickoff_member_id} is niet exact eenduidig")
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        if len(candidates) != 1:
            raise AdfDraftAdapterError(f"ADF-autocomplete voor {kickoff_member_id} is niet exact eenduidig")
        await candidates[0].click()
        # The option click only starts another ADF partial refresh. Do not
        # click the add button until the input reflects the exact selection.
        selected_value = ""
        for _ in range(AUTOCOMPLETE_SETTLE_ATTEMPTS):
            selected_value = " ".join((await field.input_value()).split())
            if is_exact_autocomplete_match(reference.display_name, selected_value):
                break
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        if not is_exact_autocomplete_match(reference.display_name, selected_value):
            raise AdfDraftAdapterError(f"ADF-autocompletekeuze voor {kickoff_member_id} werd niet bevestigd")
        add_button = await self._one_from_names("button", self._labels.add_buttons)
        await add_button.click()
        await self._wait_for_row_presence(kickoff_member_id, True)

    async def remove_player(self, kickoff_member_id: str) -> None:
        await self._open_players()
        row = await self._row(kickoff_member_id)
        assert row is not None
        # On the live ADF page the per-row delete operation is an unlabeled
        # link with its ``DeleteTrash.png`` icon in the first cell (not the
        # top-level bulk-delete action). Restricting the locator to that cell
        # and icon keeps this action row-local and rejects a layout change.
        cells = row.locator("td")
        if await cells.count() < 1:
            raise AdfDraftAdapterError(f"ADF-spelersrij voor {kickoff_member_id} heeft geen cellen")
        remove_link = cells.nth(0).locator("a")
        if await remove_link.count() != 1:
            raise AdfDraftAdapterError(f"ADF-verwijdercontrol voor {kickoff_member_id} ontbreekt of is dubbel")
        icon = remove_link.locator("img")
        if await icon.count() != 1 or not is_row_delete_icon(await icon.first.get_attribute("src")):
            raise AdfDraftAdapterError(f"ADF-verwijdercontrol voor {kickoff_member_id} is niet de rij-prullenbak")
        await remove_link.click()
        await self._wait_for_row_presence(kickoff_member_id, False)

    async def set_shirt_number(self, kickoff_member_id: str, shirt_number: int) -> None:
        await self._open_players()
        row = await self._row(kickoff_member_id)
        assert row is not None
        fields = row.locator("input[type=text], input[type=number]")
        if await fields.count() != 1:
            raise AdfDraftAdapterError(f"ADF-rugnummercontrol voor {kickoff_member_id} veranderde; stop veilig")
        await fields.first.fill(str(shirt_number))
        await fields.first.press("Tab")
        await self._wait_for_shirt_number(kickoff_member_id, shirt_number)

    async def set_goalkeeper(self, kickoff_member_id: str, goalkeeper: bool) -> None:
        if not goalkeeper:
            raise AdfDraftAdapterError("doelman uitschakelen is geen toegelaten draftactie")
        await self._open_players()
        row = await self._row(kickoff_member_id)
        assert row is not None
        _captain, goalkeeper_box = await self._role_controls(row, kickoff_member_id)
        if not await goalkeeper_box.is_checked():
            await goalkeeper_box.check()
            await self._wait_for_role(kickoff_member_id, "goalkeeper")

    async def set_captain(self, kickoff_member_id: str) -> None:
        await self._open_players()
        row = await self._row(kickoff_member_id)
        assert row is not None
        captain_box, _goalkeeper = await self._role_controls(row, kickoff_member_id)
        if not await captain_box.is_checked():
            await captain_box.check()
            await self._wait_for_role(kickoff_member_id, "captain")

class AdfDraftAdapterTests(unittest.TestCase):
    def test_reference_requires_nonempty_opaque_id_and_display_name(self) -> None:
        with self.assertRaisesRegex(AdfDraftAdapterError, "lege"):
            PlayerReference("", "Example")
        with self.assertRaisesRegex(AdfDraftAdapterError, "lege"):
            PlayerReference("EK-1", " ")

    def test_adapter_has_only_draft_protocol_mutators(self) -> None:
        public = {name for name in dir(AdfDraftAdapter) if not name.startswith("_")}
        self.assertEqual(
            public & {"add_player", "remove_player", "set_shirt_number", "set_goalkeeper", "set_captain", "save_draft"},
            {"add_player", "remove_player", "set_shirt_number", "set_goalkeeper", "set_captain"},
        )
        self.assertNotIn("save_draft", public)
        self.assertNotIn("submit_official", public)

    def test_autocomplete_rejects_partial_and_accepts_the_single_exact_name(self) -> None:
        self.assertTrue(is_exact_autocomplete_match("Example Player", "Example Player ( 01-01-2010 )"))
        self.assertTrue(is_exact_autocomplete_match("Example Player", "Example Player"))
        self.assertFalse(is_exact_autocomplete_match("Example Player", "Example Player Jr ( 01-01-2010 )"))
        self.assertFalse(is_exact_autocomplete_match("Example Player", "Other Example Player ( 01-01-2010 )"))

    def test_exact_options_ignores_a_transient_empty_or_nonmatching_popup(self) -> None:
        option = object()
        self.assertEqual(exact_autocomplete_options("Example Player", []), [])
        self.assertEqual(exact_autocomplete_options("Example Player", [(option, "Er zijn geen resultaten gevonden.")]), [])
        self.assertEqual(
            exact_autocomplete_options("Example Player", [(option, "Example Player ( 01-01-2010 )")]),
            [option],
        )

    def test_outer_adf_player_container_is_not_a_player_row(self) -> None:
        self.assertFalse(is_real_player_row(1, 1, 2, 51))
        self.assertTrue(is_real_player_row(1, 1, 1, 8))
        with self.assertRaisesRegex(AdfDraftAdapterError, "spelersrij veranderde"):
            is_real_player_row(1, 1, 2, 8)

    def test_adf_popup_id_with_colons_uses_an_attribute_selector(self) -> None:
        self.assertEqual(adf_id_locator("MR1:0:pt1:it2::_afrautosuggestpopup"), '[id="MR1:0:pt1:it2::_afrautosuggestpopup"]')
        with self.assertRaisesRegex(AdfDraftAdapterError, "onverwacht formaat"):
            adf_id_locator('bad"]#other')

    def test_only_the_reviewed_row_trash_icon_is_a_delete_control(self) -> None:
        self.assertTrue(is_row_delete_icon("/adf/images/DeleteTrash.png"))
        self.assertFalse(is_row_delete_icon("/adf/images/DeleteAll.png"))

    def test_opaque_kickoff_id_is_stable_and_does_not_echo_display_values(self) -> None:
        member_id = opaque_kickoff_member_id("Example Player", "01-01-2010")
        self.assertEqual(member_id, opaque_kickoff_member_id("Example Player", "01-01-2010"))
        self.assertNotIn("Example", member_id)

    def test_verified_player_identity_columns_follow_the_shirt_number_field(self) -> None:
        self.assertEqual((PLAYER_ROW_NAME_COLUMN, PLAYER_ROW_BIRTH_DATE_COLUMN), (3, 4))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AdfDraftAdapterTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
