"""Pure eligibility/recheck/report guards; evidence verification belongs to the runner."""
from datetime import date, datetime


def confirmed_exclusion(review):
    """Only a source-backed institutional exclusion can suppress a BIU-wide check."""
    if review.get("institution_status") != "excluded" or review.get("evidence_verified") is not True:
        return False
    if not all(isinstance(review.get(k), str) and review[k].strip()
               for k in ("call_id", "cycle", "reason", "official_source", "verified_at")):
        return False
    if not review["official_source"].startswith("https://"):
        return False
    try:
        observed = datetime.fromisoformat(review["verified_at"])
    except ValueError:
        return False
    return observed.utcoffset() is not None


def should_recheck(call_id, cycle, review, evidence_changed=False):
    """A suppression never applies to another call/cycle or an observed rule change."""
    if not call_id or not cycle or evidence_changed:
        return True
    return not (review.get("call_id") == call_id and review.get("cycle") == cycle
                and confirmed_exclusion(review))


def restricted_footer(records, report_day):
    """Upcoming stored dates stay visible even when an excluded cycle is not fetched."""
    today = date.fromisoformat(report_day)
    lines = []
    for r in records:
        if r.get("otherwise_relevant") is not True:
            continue
        institutional = r.get("institution_status", "unknown")
        personal = r.get("personal_status", "unknown")
        if institutional not in {"excluded", "restricted", "unknown"} and personal not in {"restricted", "unknown"}:
            continue
        upcoming = []
        for milestone in r.get("deadlines", []):
            # ISO calendar dates are separate from source text/timezone. Unknown dates
            # cannot establish that a call is upcoming and remain in the review queue.
            try:
                d = date.fromisoformat(milestone["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if d >= today:
                upcoming.append(f"{milestone.get('stage', 'stage unknown')}: {d.isoformat()} "
                                f"({milestone.get('certainty', 'unverified')}; "
                                f"{milestone.get('timezone', 'timezone unknown')})")
        if not upcoming:
            continue
        lines.append(f"- {r.get('name', r.get('call_id', 'Unnamed call'))} | cycle {r.get('cycle', 'unknown')}\n"
                     f"  Institution: {institutional}; personal: {personal}. "
                     f"{r.get('reason') or 'Applicability not yet verified.'}\n"
                     f"  Dates: {'; '.join(upcoming)}\n"
                     f"  Evidence: {r.get('official_source') or 'not yet verified'}; "
                     f"checked {r.get('verified_at') or 'not yet checked'}")
    if not lines:
        return ""
    return "\n\nUpcoming calls with restrictions or unresolved eligibility\n" + "\n".join(lines)
