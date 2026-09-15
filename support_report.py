"""Privacy-preserving, user-reviewed GitHub support reports.

This module deliberately has no HTTP client and never writes a diagnostic log.
It classifies an exception locally, then can open a GitHub *new issue* form
only after the user explicitly opted in.  GitHub receives only fixed category
values; neither the exception text nor match, player, staff, browser or login
data can enter the URL.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlencode
import webbrowser


ISSUE_FORM_URL = "https://github.com/timvw/wedstrijdblad-sync/issues/new"
REPORT_SCHEMA = "wedstrijdblad-sync-support-v1"
SAFE_STAGES = frozenset({"psd-read", "target-read", "draft-apply"})
SAFE_CATEGORIES = frozenset(
    {
        "authentication-or-session",
        "browser-timeout",
        "connection",
        "safety-stop",
        "unsupported-or-ambiguous-interface",
        "unexpected",
    }
)
_SAFE_VALUE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


@dataclass(frozen=True)
class SupportReport:
    """The complete data set that is eligible for transmission to GitHub."""

    schema: str
    stage: str
    category: str
    exception_family: str


def _category(exception: BaseException) -> str:
    """Classify locally. Exception text is never returned or serialised."""
    detail = str(exception).casefold()
    if any(
        word in detail
        for word in ("login", "session", "page expired", "authentication", "aanmeld")
    ):
        return "authentication-or-session"
    if "timeout" in detail:
        return "browser-timeout"
    if any(word in detail for word in ("network", "connection", "verbinding")):
        return "connection"
    if any(word in detail for word in ("protected", "bescherm", "safety", "veilig")):
        return "safety-stop"
    if any(
        word in detail
        for word in (
            "ambiguous",
            "dubbelzinnig",
            "ontbreekt",
            "onbekend",
            "unexpected layout",
        )
    ):
        return "unsupported-or-ambiguous-interface"
    return "unexpected"


def _exception_family(exception: BaseException) -> str:
    """Return a fixed class family instead of an arbitrary exception class name."""
    if isinstance(exception, TimeoutError):
        return "timeout-error"
    if isinstance(exception, (ValueError, KeyError, TypeError)):
        return "validation-error"
    if isinstance(exception, OSError):
        return "os-error"
    return "application-error"


def make_support_report(stage: str, exception: BaseException) -> SupportReport:
    """Create an allow-listed support report without preserving exception text."""
    if stage not in SAFE_STAGES:
        raise ValueError("unknown support-report stage")
    report = SupportReport(
        REPORT_SCHEMA, stage, _category(exception), _exception_family(exception)
    )
    validate_report(report)
    return report


def validate_report(report: SupportReport) -> None:
    """Fail closed if a future caller tries to add arbitrary diagnostic values."""
    if (
        report.schema != REPORT_SCHEMA
        or report.stage not in SAFE_STAGES
        or report.category not in SAFE_CATEGORIES
    ):
        raise ValueError("unsafe support report")
    if not _SAFE_VALUE.fullmatch(report.exception_family):
        raise ValueError("unsafe support report exception family")


def issue_url(report: SupportReport) -> str:
    """Build a reviewable GitHub issue URL from the fixed safe fields only."""
    validate_report(report)
    title = f"[support] {report.stage}: {report.category}"
    body = "\n".join(
        (
            "Automatisch gegenereerde, privacyvriendelijke supportmelding.",
            "",
            f"- schema: `{report.schema}`",
            f"- stap: `{report.stage}`",
            f"- categorie: `{report.category}`",
            f"- foutfamilie: `{report.exception_family}`",
            "",
            "Bevestiging voor indiening:",
            "- Ik heb geen wachtwoorden, cookies, URLs, wedstrijd-ID's, namen, geboortedata of screenshots met persoonsgegevens toegevoegd.",
            "- Voor een mogelijk beveiligings- of privacyprobleem dien ik dit niet publiek in; ik volg SECURITY.md.",
        )
    )
    return f"{ISSUE_FORM_URL}?{urlencode({'title': title, 'body': body})}"


def open_reviewed_issue(report: SupportReport) -> bool:
    """Open a prefilled form; this never creates or submits an issue."""
    return bool(webbrowser.open(issue_url(report), new=2))
