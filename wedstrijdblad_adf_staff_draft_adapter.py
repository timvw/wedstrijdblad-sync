#!/usr/bin/env python3
"""Narrow, no-save ADF adapter for a reviewed e-Kickoff staff draft.

All identity data is temporary.  This adapter intentionally has no official
submission method and no generic click escape hatch.
"""

from __future__ import annotations

from typing import Any, Mapping
import unittest

import psd_reader
from wedstrijdblad_adf_draft_adapter import (
    ADF_ACTION_SETTLE_ATTEMPTS,
    AUTOCOMPLETE_KEY_DELAY_MS,
    AUTOCOMPLETE_SETTLE_ATTEMPTS,
    AUTOCOMPLETE_SETTLE_INTERVAL_MS,
    AdfDraftAdapterError,
    adf_id_locator,
    exact_autocomplete_options,
    is_exact_autocomplete_match,
    is_row_delete_icon,
)
from wedstrijdblad_adf_staff_reader import (
    AdfStaffReader,
    is_real_staff_row,
)
from wedstrijdblad_staff_sync import KickoffStaff, StaffReference


STAFF_FIELD_LABELS = ("Staff", "Staf")
STAFF_ADD_LABELS = ("This staff", "Deze staf")


class AdfStaffDraftAdapter(AdfStaffReader):
    """Strict staff writer for a fully mapped, reviewed roster only."""

    def __init__(self, page: Any, references: Mapping[str, StaffReference]) -> None:
        super().__init__(page)
        if not references:
            raise AdfDraftAdapterError("geen expliciete e-Kickoff-stafkoppelingen")
        if set(references) != {reference.member_id for reference in references.values()}:
            raise AdfDraftAdapterError("ongeldige e-Kickoff-stafkoppelingen")
        if len({reference.display_name.casefold() for reference in references.values()}) != len(references):
            raise AdfDraftAdapterError("dubbele zichtbare stafnaam; handmatige resolutie vereist")
        self._references = dict(references)

    def _reference(self, member_id: str) -> StaffReference:
        try:
            return self._references[member_id]
        except KeyError as exc:
            raise AdfDraftAdapterError(f"onbekende opaque e-Kickoff-staf-ID: {member_id}") from exc

    async def _one(self, role: str, labels: tuple[str, ...]) -> Any:
        found = []
        for label in labels:
            locator = self._page.get_by_role(role, name=label, exact=True)
            if await locator.count() == 1:
                found.append(locator)
            elif await locator.count() > 1:
                raise AdfDraftAdapterError(f"ADF-{role}-control is dubbelzinnig")
        if len(found) != 1:
            raise AdfDraftAdapterError(f"ADF-{role}-control ontbreekt of is dubbelzinnig")
        return found[0]

    async def _row(self, member_id: str, *, required: bool = True) -> Any | None:
        reference = self._reference(member_id)
        rows = self._page.locator("tr:visible").filter(has_text=reference.display_name)
        if reference.birth_date:
            rows = rows.filter(has_text=reference.birth_date)
        matches: list[Any] = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            cells = row.locator("td")
            select = row.locator("select")
            if await cells.count() < 1 or await select.count() != 1:
                continue
            icon = cells.nth(0).locator("a img")
            source = await icon.first.get_attribute("src") if await icon.count() == 1 else None
            if is_real_staff_row(await cells.count(), await select.count(), source):
                matches.append(row)
        if not matches and not required:
            return None
        if len(matches) != 1:
            raise AdfDraftAdapterError(f"ADF-stafrij voor {member_id} is niet eenduidig")
        return matches[0]

    async def _wait_for_row(self, member_id: str, present: bool) -> Any | None:
        for _ in range(ADF_ACTION_SETTLE_ATTEMPTS):
            try:
                row = await self._row(member_id, required=False)
            except AdfDraftAdapterError:
                row = None
            if (row is not None) == present:
                return row
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        state = "verschijnen" if present else "verdwijnen"
        raise AdfDraftAdapterError(f"ADF-stafrij voor {member_id} bleef niet {state}")

    async def read_staff(self) -> tuple[KickoffStaff, ...]:
        await self._open_staff()
        actual = await self.discover_staff()
        actual_by_id = {item.member_id: item for item in actual}
        # Prevent a diff from silently removing a staff row whose identity was
        # not explicitly selected in the mapping dialog.
        if set(actual_by_id) - set(self._references):
            raise AdfDraftAdapterError("onbekend of ambigue staflid op doelblad; eerst identiteiten koppelen")
        return tuple(
            sorted(
                (
                    KickoffStaff(
                        item.member_id,
                        psd_reader.canonical_staff_function(item.function) if item.function else "",
                    )
                    for item in actual
                ),
                key=lambda x: x.member_id,
            )
        )

    async def add_staff(self, member_id: str) -> None:
        await self._open_staff()
        reference = self._reference(member_id)
        field = await self._one("combobox", STAFF_FIELD_LABELS)
        await field.click()
        await field.fill("")
        await field.press_sequentially(reference.display_name, delay=AUTOCOMPLETE_KEY_DELAY_MS)
        popup_id = await field.get_attribute("aria-owns")
        if not popup_id:
            raise AdfDraftAdapterError("ADF-staf-autocompletepopup ontbreekt")
        popup = self._page.locator(adf_id_locator(popup_id))
        await popup.wait_for(state="visible", timeout=10_000)
        options = popup.locator("[role=option], li, a")
        candidates: list[Any] = []
        for _ in range(AUTOCOMPLETE_SETTLE_ATTEMPTS):
            visible = [(options.nth(i), " ".join((await options.nth(i).inner_text()).split())) for i in range(await options.count())]
            candidates = exact_autocomplete_options(reference.display_name, visible)
            if len(candidates) == 1:
                break
            if len(candidates) > 1:
                raise AdfDraftAdapterError(f"ADF-staf-autocomplete voor {member_id} is niet exact eenduidig")
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        if len(candidates) != 1:
            raise AdfDraftAdapterError(f"ADF-staf-autocomplete voor {member_id} is niet exact eenduidig")
        await candidates[0].click()
        for _ in range(AUTOCOMPLETE_SETTLE_ATTEMPTS):
            if is_exact_autocomplete_match(reference.display_name, " ".join((await field.input_value()).split())):
                break
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        else:
            raise AdfDraftAdapterError(f"ADF-staf-autocompletekeuze voor {member_id} werd niet bevestigd")
        await (await self._one("button", STAFF_ADD_LABELS)).click()
        await self._wait_for_row(member_id, True)

    async def remove_staff(self, member_id: str) -> None:
        await self._open_staff()
        row = await self._row(member_id)
        assert row is not None
        link = row.locator("td").nth(0).locator("a")
        icon = link.locator("img")
        if await link.count() != 1 or await icon.count() != 1 or not is_row_delete_icon(await icon.first.get_attribute("src")):
            raise AdfDraftAdapterError(f"ADF-verwijdercontrol voor {member_id} is niet de rij-prullenbak")
        await link.click()
        await self._wait_for_row(member_id, False)

    async def set_function(self, member_id: str, function: str) -> None:
        canonical = psd_reader.canonical_staff_function(function)
        await self._open_staff()
        row = await self._row(member_id)
        assert row is not None
        select = row.locator("select")
        if await select.count() != 1:
            raise AdfDraftAdapterError(f"ADF-staffunctiecontrol voor {member_id} veranderde")
        options = select.first.locator("option")
        matches = []
        for index in range(await options.count()):
            option = options.nth(index)
            try:
                option_function = psd_reader.canonical_staff_function(" ".join((await option.inner_text()).split()))
            except psd_reader.SnapshotError:
                continue
            if option_function == canonical:
                matches.append(option)
        if len(matches) != 1:
            raise AdfDraftAdapterError(f"ADF-staffunctie voor {member_id} is niet eenduidig")
        value = await matches[0].get_attribute("value")
        if value is None:
            raise AdfDraftAdapterError(f"ADF-staffunctie voor {member_id} mist waarde")
        await select.first.select_option(value=value)
        for _ in range(ADF_ACTION_SETTLE_ATTEMPTS):
            current = await self._wait_for_row(member_id, True)
            assert current is not None
            selected = current.locator("select option:checked")
            if await selected.count() == 1:
                try:
                    selected_function = psd_reader.canonical_staff_function(" ".join((await selected.inner_text()).split()))
                except psd_reader.SnapshotError:
                    selected_function = ""
                if selected_function == canonical:
                    return
            await self._page.wait_for_timeout(AUTOCOMPLETE_SETTLE_INTERVAL_MS)
        raise AdfDraftAdapterError(f"ADF-staffunctie voor {member_id} werd niet bevestigd")


class AdfStaffDraftAdapterTests(unittest.TestCase):
    def test_adapter_has_no_save_or_submission_method(self) -> None:
        public = {name for name in dir(AdfStaffDraftAdapter) if not name.startswith("_")}
        self.assertEqual(public & {"add_staff", "remove_staff", "set_function", "save", "send"}, {"add_staff", "remove_staff", "set_function"})


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AdfStaffDraftAdapterTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
