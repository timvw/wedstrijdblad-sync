#!/usr/bin/env python3
"""Deterministic, user-confirmed identity mapping for PSD and e-Kickoff.

Names and birth dates are presentation data in a future mapping UI only. This
module accepts and serializes opaque IDs only. It never applies fuzzy matching:
every PSD member needs one explicit e-Kickoff member, and vice versa.
"""

from __future__ import annotations

import unittest
from dataclasses import asdict, dataclass
from typing import Iterable


class IdentityResolutionError(ValueError):
    """A mapping is incomplete, ambiguous, or unsafe to use."""


@dataclass(frozen=True)
class IdentityMapping:
    psd_member_id: str
    kickoff_member_id: str

    def __post_init__(self) -> None:
        if not self.psd_member_id or not self.kickoff_member_id:
            raise IdentityResolutionError("lege PSD- of e-Kickoff-ID is niet toegestaan")


def resolve_identities(
    psd_member_ids: Iterable[str], kickoff_member_ids: Iterable[str], choices: Iterable[IdentityMapping]
) -> tuple[IdentityMapping, ...]:
    """Validate an exact one-to-one mapping; order is stable for reproducible plans."""
    psd_ids = tuple(psd_member_ids)
    kickoff_ids = tuple(kickoff_member_ids)
    if len(set(psd_ids)) != len(psd_ids) or len(set(kickoff_ids)) != len(kickoff_ids):
        raise IdentityResolutionError("bron bevat dubbele opaque member-ID's")
    if not psd_ids:
        return tuple()
    choice_by_psd: dict[str, str] = {}
    used_kickoff: set[str] = set()
    for choice in choices:
        if choice.psd_member_id not in psd_ids:
            raise IdentityResolutionError("mapping bevat een speler die niet in de PSD-selectie zit")
        if choice.kickoff_member_id not in kickoff_ids:
            raise IdentityResolutionError("mapping bevat een speler die niet in e-Kickoff beschikbaar is")
        if choice.psd_member_id in choice_by_psd:
            raise IdentityResolutionError("PSD-speler is meer dan eenmaal gekoppeld")
        if choice.kickoff_member_id in used_kickoff:
            raise IdentityResolutionError("e-Kickoff-speler is meer dan eenmaal gekoppeld")
        choice_by_psd[choice.psd_member_id] = choice.kickoff_member_id
        used_kickoff.add(choice.kickoff_member_id)
    missing = sorted(set(psd_ids) - set(choice_by_psd))
    if missing:
        raise IdentityResolutionError(f"identiteitskoppeling ontbreekt voor {len(missing)} PSD-speler(s)")
    return tuple(IdentityMapping(member_id, choice_by_psd[member_id]) for member_id in sorted(psd_ids))


def remap_kickoff_to_psd(kickoff_member_id: str, mappings: Iterable[IdentityMapping]) -> str:
    """Stop rather than emit an unmatched update or removal."""
    matches = [mapping.psd_member_id for mapping in mappings if mapping.kickoff_member_id == kickoff_member_id]
    if len(matches) != 1:
        raise IdentityResolutionError("e-Kickoff-speler heeft geen eenduidige expliciete PSD-koppeling")
    return matches[0]


def serialise_mappings(mappings: Iterable[IdentityMapping]) -> list[dict[str, str]]:
    """A disk-safe representation: only opaque values, never display labels."""
    return [asdict(mapping) for mapping in mappings]


class IdentityResolutionTests(unittest.TestCase):
    def test_complete_one_to_one_mapping_is_stable(self) -> None:
        resolved = resolve_identities(
            ("PSD-A", "PSD-B"), ("EK-1", "EK-2"), (IdentityMapping("PSD-B", "EK-2"), IdentityMapping("PSD-A", "EK-1"))
        )
        self.assertEqual(resolved, (IdentityMapping("PSD-A", "EK-1"), IdentityMapping("PSD-B", "EK-2")))

    def test_missing_mapping_is_rejected(self) -> None:
        with self.assertRaisesRegex(IdentityResolutionError, "ontbreekt"):
            resolve_identities(("PSD-A", "PSD-B"), ("EK-1", "EK-2"), (IdentityMapping("PSD-A", "EK-1"),))

    def test_one_kickoff_member_cannot_be_used_twice(self) -> None:
        with self.assertRaisesRegex(IdentityResolutionError, "meer dan eenmaal"):
            resolve_identities(
                ("PSD-A", "PSD-B"), ("EK-1",), (IdentityMapping("PSD-A", "EK-1"), IdentityMapping("PSD-B", "EK-1"))
            )

    def test_unresolved_target_never_becomes_a_guess(self) -> None:
        with self.assertRaisesRegex(IdentityResolutionError, "geen eenduidige"):
            remap_kickoff_to_psd("EK-404", (IdentityMapping("PSD-A", "EK-1"),))

    def test_serialized_mapping_has_no_display_fields(self) -> None:
        self.assertEqual(
            serialise_mappings((IdentityMapping("PSD-A", "EK-1"),)),
            [{"psd_member_id": "PSD-A", "kickoff_member_id": "EK-1"}],
        )


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(IdentityResolutionTests)
    )
    raise SystemExit(0 if result.wasSuccessful() else 1)
