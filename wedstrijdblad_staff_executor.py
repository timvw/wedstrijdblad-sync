#!/usr/bin/env python3
"""Sequential, reread-verified executor for a reviewed staff diff.

It intentionally does not contain save or official-submission behaviour.
"""

from __future__ import annotations

from typing import Protocol, Sequence
import unittest

import psd_reader
from wedstrijdblad_draft_executor import DraftExecutionError
from wedstrijdblad_staff_sync import KickoffStaff


class StaffDraftAdapter(Protocol):
    async def read_staff(self) -> tuple[KickoffStaff, ...]: ...
    async def add_staff(self, member_id: str) -> None: ...
    async def remove_staff(self, member_id: str) -> None: ...
    async def set_function(self, member_id: str, function: str) -> None: ...


def validate_desired_staff(staff: Sequence[KickoffStaff]) -> tuple[KickoffStaff, ...]:
    if not staff:
        raise DraftExecutionError("lege stafselectie wordt niet automatisch toegepast")
    result = []
    ids = set()
    for member in staff:
        if not member.member_id or member.member_id in ids:
            raise DraftExecutionError("dubbele of lege stafidentiteit")
        ids.add(member.member_id)
        result.append(KickoffStaff(member.member_id, psd_reader.canonical_staff_function(member.function)))
    return tuple(sorted(result, key=lambda item: item.member_id))


async def execute_staff_actions(adapter: StaffDraftAdapter, desired: Sequence[KickoffStaff]) -> tuple[str, ...]:
    """Apply exactly one verified row-local staff mutation at a time."""
    desired = validate_desired_staff(desired)
    current = {item.member_id: item for item in await adapter.read_staff()}
    target = {item.member_id: item for item in desired}
    actions: list[str] = []
    for member_id in sorted(set(current) - set(target)):
        await adapter.remove_staff(member_id)
        if member_id in {item.member_id for item in await adapter.read_staff()}:
            raise DraftExecutionError(f"ADF bevestigt verwijderen van staflid {member_id} niet")
        actions.append("staff_remove")
    for member_id in sorted(set(target) - set(current)):
        await adapter.add_staff(member_id)
        if member_id not in {item.member_id for item in await adapter.read_staff()}:
            raise DraftExecutionError(f"staflid {member_id} ontbreekt na ADF-herlezing")
        actions.append("staff_add")
    for member_id in sorted(target):
        before = {item.member_id: item for item in await adapter.read_staff()}
        if member_id not in before:
            raise DraftExecutionError(f"staflid {member_id} ontbreekt vóór functietoewijzing")
        if before[member_id].function != target[member_id].function:
            await adapter.set_function(member_id, target[member_id].function)
            after = {item.member_id: item for item in await adapter.read_staff()}
            if after.get(member_id) != target[member_id]:
                raise DraftExecutionError(f"ADF bevestigt staffunctie voor {member_id} niet")
            actions.append("staff_function")
    final = {item.member_id: item for item in await adapter.read_staff()}
    if final != target:
        raise DraftExecutionError("staf-eindherlezing wijkt af van de goedgekeurde selectie")
    return tuple(actions)


class FakeStaffAdapter:
    def __init__(self, staff: Sequence[KickoffStaff] = ()) -> None:
        self.staff = {item.member_id: item for item in staff}
        self.calls: list[str] = []

    async def read_staff(self) -> tuple[KickoffStaff, ...]:
        self.calls.append("read")
        return tuple(sorted(self.staff.values(), key=lambda item: item.member_id))

    async def add_staff(self, member_id: str) -> None:
        self.calls.append(f"add:{member_id}")
        self.staff[member_id] = KickoffStaff(member_id, "")

    async def remove_staff(self, member_id: str) -> None:
        self.calls.append(f"remove:{member_id}")
        self.staff.pop(member_id, None)

    async def set_function(self, member_id: str, function: str) -> None:
        self.calls.append(f"function:{member_id}:{function}")
        self.staff[member_id] = KickoffStaff(member_id, function)


class StaffExecutorTests(unittest.TestCase):
    def test_actions_are_row_local_and_reread(self) -> None:
        adapter = FakeStaffAdapter((KickoffStaff("EK-S-old", "T2"),))
        desired = (KickoffStaff("EK-S-1", "T1"),)
        import asyncio
        actions = asyncio.run(execute_staff_actions(adapter, desired))
        self.assertEqual(actions, ("staff_remove", "staff_add", "staff_function"))
        self.assertNotIn("save", " ".join(adapter.calls).casefold())
        self.assertGreater(adapter.calls.count("read"), len(actions))

    def test_empty_staff_is_blocked(self) -> None:
        import asyncio
        with self.assertRaisesRegex(DraftExecutionError, "lege stafselectie"):
            asyncio.run(execute_staff_actions(FakeStaffAdapter(), ()))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(StaffExecutorTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
