"""Deterministic, offline rendering of already-classified Grant Watch observations.

Only display fields are read. Source verification and eligibility routing remain
the caller's responsibility; templates never enter routine model context.
"""
import html
import unicodedata
from datetime import date
from pathlib import Path
from string import Template
from urllib.parse import urlsplit


ASSETS = Path(__file__).resolve().parents[1] / "skills/monitor-grant-calls/assets/email"
MONTHS = ("ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי",
          "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר")
SECTIONS = ("main", "unverified_lead", "non_eligible")


def _text(value):
    if not isinstance(value, str):
        raise ValueError("Display fields must be plain strings")
    return html.escape(value, quote=True).replace("\n", "<br>")


def _lines(value):
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [item for item in value if item.strip()]
    raise ValueError("Expected plain text or a list of plain text fields")


def _div(value, style=""):
    escaped = _text(value)
    # Match the approved English rows while supporting Hebrew/mixed text.
    direction = next(("rtl" if b in {"R", "AL"} else "ltr"
                      for c in value if (b := unicodedata.bidirectional(c)) in {"R", "AL", "L"}), "rtl")
    align = "right" if direction == "rtl" else "left"
    return f'<div dir="{direction}" style="text-align:{align};{style}">{escaped}</div>'


def _url(value):
    if (not isinstance(value, str) or not value
            or any(c.isspace() or unicodedata.category(c).startswith("C") for c in value)
            or "\\" in value):
        raise ValueError("Source links must be absolute HTTP(S) URLs")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise ValueError
        parsed.port  # Reject malformed ports too.
    except ValueError:
        raise ValueError("Source links must be absolute HTTP(S) URLs without credentials") from None
    return html.escape(value, quote=True)


def _template(filename, **values):
    return Template((ASSETS / filename).read_text(encoding="utf-8")).substitute(values)


def _hebrew_date(day, year=True):
    return f"{day.day} ב{MONTHS[day.month - 1]}" + (f" {day.year}" if year else "")


def _deadline(row):
    if row.get("deadline_text"):
        return row["deadline_text"]
    parts = []
    for milestone in row.get("deadlines", []):
        if not isinstance(milestone, dict):
            raise ValueError("Deadlines must be objects")
        # Preserve certainty and timezones; never advance a recurring date/year.
        parts.append(" | ".join(_lines([milestone.get(k, "") for k in
                     ("stage", "date", "time", "timezone", "certainty", "source_text")])))
    return "; ".join(parts)


def _row(row, section):
    if not isinstance(row, dict):
        raise ValueError("Report rows must be objects")
    name = row.get("name", row.get("call_id"))
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Each report row needs a name or call_id")
    detail = []
    # Explicit allowlist: audit/request logs, raw HTML and provenance are not email.
    for field in ("summary", "reason", "caveats"):
        detail.extend(_lines(row.get(field, "")))
    if not detail:
        raise ValueError("Each report row needs a summary, reason or caveat")
    urls = [row["official_source"]] if row.get("official_source") else []
    extra = row.get("source_urls", [])
    if not isinstance(extra, list):
        raise ValueError("source_urls must be a list")
    urls.extend(extra)
    if not urls:
        raise ValueError("Each report row needs a source link")
    links = []
    for safe in dict.fromkeys(_url(url) for url in urls):
        links.append(f'<a href="{safe}" style="color:#104d76;text-decoration:underline;font-weight:bold">למקור הרשמי</a>')
    deadline = _deadline(row)
    return _template("row.html",
                     background="#fff7e7" if section == "unverified_lead" else "#f6f9fb",
                     accent="#c68020" if section == "unverified_lead" else "#408b85",
                     deadline=_div(deadline, "color:#155e59;font-weight:bold") if deadline else "",
                     name=_div(name, ("font-size:17px;" if section == "main" else "")
                               + "font-weight:bold;color:#142c41"),
                     details="".join(_div(text) for text in detail), links=" · ".join(links))


def _highlight(rows, today):
    upcoming = []
    for index, row in enumerate(rows):
        # Only explicit date-verified milestones in the verified section count.
        dates = {date.fromisoformat(m["date"]) for m in row.get("deadlines", [])
                 if m.get("certainty") == "verified" and m.get("date")}
        upcoming.extend((d, index) for d in dates if d >= today)
    if not upcoming:
        return ""
    nearest = min(d for d, _ in upcoming)
    count = len({i for d, i in upcoming if d == nearest})
    numbers = {2: "שני", 3: "שלושה", 4: "ארבעה", 5: "חמישה", 6: "שישה"}
    line = ("קול קורא אחד עם מועד ביום זה." if count == 1 else
            f"{numbers.get(count, str(count))} קולות קוראים עם מועדים באותו יום.")
    return _template("highlight.html", date=_hebrew_date(nearest, nearest.year != today.year), count=line)


def render_digest(report, report_day):
    """Render a structured report without network, clock, model or delivery calls."""
    today = date.fromisoformat(report_day)
    if not isinstance(report, dict) or report.get("state") not in {"complete", "partial", "failed", "blocked"}:
        raise ValueError("A report with an explicit coverage state is required")
    sections = {}
    for section in SECTIONS:
        rows = report.get(section, [])
        if not isinstance(rows, list):
            raise ValueError("Report sections must be lists")
        sections[section] = "".join(_row(row, section) for row in rows)
    summary = _lines(report.get("summary", ""))
    if report["state"] != "complete" and not summary:
        summary = ["הבדיקה לא הושלמה; הכיסוי חלקי ואין להסיק שלא נמצאו שינויים."]
    if not sections["main"]:
        sections["main"] = _div("לא נמצאו עדכונים מאומתים בבדיקה שהושלמה." if report["state"] == "complete"
                                else "אין ברשימה מועדים שאומתו; הבדיקה לא הושלמה.")
    if not sections["unverified_lead"]:
        sections["unverified_lead"] = _div("אין ברשימה הנוכחית פריטים הממתינים לאימות.")
    if not sections["non_eligible"]:
        sections["non_eligible"] = _div("None established in this bounded review. A restriction on the lead institution is not treated as exclusion where a partner route may be possible.")
    return _template("report.html", report_date=_hebrew_date(today), run_key="grant-watch:" + report_day,
                     summary="".join(_div(line, "margin:8px 0") for line in summary),
                     highlight=_highlight(report.get("main", []), today), **sections)
