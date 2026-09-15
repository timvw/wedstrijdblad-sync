#!/usr/bin/env python3
"""Read existing e-Kickoff staff rows for an explicit, temporary mapping UI.

This module deliberately has no add, delete, role-change, save or submission
method.  It supplies the missing safe input for a later staff-mapping screen:
the person is shown to the user only in memory and represented elsewhere by a
deterministic opaque ID.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any
import unittest

import wedstrijdblad_browser as browser
from wedstrijdblad_adf_draft_adapter import AdfDraftAdapterError, is_row_delete_icon


STAFF_NAME_COLUMN = 1
STAFF_BIRTH_DATE_COLUMN = 2


@dataclass(frozen=True)
class DiscoveredStaff:
    """One ephemeral current staff row; display data is never serialised."""

    member_id: str
    display_name: str
    birth_date: str
    function: str


def opaque_kickoff_staff_id(display_name: str, birth_date: str) -> str:
    """Make a stable in-memory handle without revealing the source text."""
    if not display_name.strip() or not birth_date.strip():
        raise AdfDraftAdapterError("e-Kickoff-staflid mist naam of geboortedatum voor expliciete koppeling")
    digest = hashlib.sha256(f"{display_name}\x00{birth_date}".encode("utf-8")).hexdigest()
    return f"EK-S-{digest[:24]}"


def is_real_staff_row(cell_count: int, select_count: int, delete_icon_src: str | None) -> bool:
    """Recognise only the compact ADF staff row observed on the live sheet."""
    return cell_count == 6 and select_count == 1 and is_row_delete_icon(delete_icon_src)


class AdfStaffReader:
    """Narrow read-only adapter for the live e-Kickoff Staff tab."""

    def __init__(self, page: Any) -> None:
        self._page = page

    async def _open_staff(self) -> None:
        await browser._dismiss_cookie_policy(self._page)
        tabs = []
        for label in browser.UI_LABELS["Staff"]:
            tab = self._page.get_by_role("tab", name=label, exact=True)
            if await tab.count() == 1:
                tabs.append(tab)
            elif await tab.count() > 1:
                raise AdfDraftAdapterError("ADF-staftab is dubbelzinnig")
        if len(tabs) != 1:
            raise AdfDraftAdapterError("ADF-staftab ontbreekt")
        tab = tabs[0]
        if await tab.get_attribute("aria-selected") != "true":
            await tab.click()
            # Staff does not have an ADF empty-state control equivalent to
            # Players. Wait only for the tab selection, never for text that
            # could contain a person's data.
            for _ in range(20):
                if await tab.get_attribute("aria-selected") == "true":
                    return
                await self._page.wait_for_timeout(250)
            raise AdfDraftAdapterError("ADF-staftab werd niet actief")

    async def discover_staff(self) -> tuple[DiscoveredStaff, ...]:
        """Return current staff for an explicit mapping UI, never a plan."""
        await self._open_staff()
        # Let ADF finish its partial refresh after a tab transition. A compact
        # staff row has a role select; outer containers do not qualify.
        for _ in range(20):
            if await self._page.locator("tr:visible select").count() or await browser._empty_selection(self._page, "staff"):
                break
            await self._page.wait_for_timeout(250)
        result: list[DiscoveredStaff] = []
        rows = self._page.locator("tr:visible")
        for index in range(await rows.count()):
            row = rows.nth(index)
            cells = row.locator("td")
            select = row.locator("select")
            if await cells.count() < 1 or await select.count() != 1:
                continue
            delete_image = cells.nth(0).locator("a img")
            image_src = await delete_image.first.get_attribute("src") if await delete_image.count() == 1 else None
            if not is_real_staff_row(await cells.count(), await select.count(), image_src):
                continue
            display_name = " ".join((await cells.nth(STAFF_NAME_COLUMN).inner_text()).split())
            birth_date = " ".join((await cells.nth(STAFF_BIRTH_DATE_COLUMN).inner_text()).split())
            function = " ".join((await select.first.locator("option:checked").inner_text()).split())
            member_id = opaque_kickoff_staff_id(display_name, birth_date)
            result.append(DiscoveredStaff(member_id, display_name, birth_date, function))
        ids = [staff.member_id for staff in result]
        if len(ids) != len(set(ids)):
            raise AdfDraftAdapterError("dubbele e-Kickoff-stafidentiteit; handmatige controle vereist")
        return tuple(result)


class AdfStaffReaderTests(unittest.TestCase):
    def test_opaque_staff_id_is_stable_and_does_not_echo_display_data(self) -> None:
        staff_id = opaque_kickoff_staff_id("Example Staff", "01-01-1980")
        self.assertEqual(staff_id, opaque_kickoff_staff_id("Example Staff", "01-01-1980"))
        self.assertNotIn("Example", staff_id)

    def test_staff_row_requires_the_exact_compact_shape_and_row_trash_icon(self) -> None:
        self.assertTrue(is_real_staff_row(6, 1, "/adf/images/DeleteTrash.png"))
        self.assertFalse(is_real_staff_row(6, 0, "/adf/images/DeleteTrash.png"))
        self.assertFalse(is_real_staff_row(6, 1, "/adf/images/DeleteAll.png"))
        self.assertFalse(is_real_staff_row(44, 1, "/adf/images/DeleteTrash.png"))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(AdfStaffReaderTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
