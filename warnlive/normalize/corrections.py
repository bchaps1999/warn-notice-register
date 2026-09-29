"""Audit upstream warn-transformer date corrections against their source cells.

Upstream transformers carry hand-written ``date_corrections`` tables that map
a raw cell to a date when the cell does not parse or fails a sanity check.
Most entries only reformat the cell ("Downsize 1/26/20" -> 2020-01-26), but
some state a fact the cell does not: a placeholder ("01/01/0001" ->
2020-01-01), a changed year ("08/15/2023, 8/22/2023" -> 2024-08-15), or a
year added to a yearless cell ("3/29" -> 2020-03-29).

``classify`` compares each correction with the dates written in its own key,
and ``sanitized_corrections`` returns the table the engine installs:

* ``keep``: the correction is the first date written in the cell, or the
  earliest of a list written out of order (for a month-only
  cell, the first of that month, kept at month precision);
* ``keep_null``: upstream already maps the cell to null;
* ``keep_linked_document``: the cell is a URL to the notice document, and
  the upstream date was transcribed from that document, not the cell. It is
  kept unchanged (we have not re-read those documents);
* ``literal``: the cell begins with a readable date inside the transformer's
  plausible window but upstream chose a different one (usually a changed
  year); the cell's own date is used;
* ``first_literal``: upstream chose a later date from a list or range
  ("November 9 through November 23, 2019"); the first date is used;
* ``null``: anything else - no readable first date, a placeholder, or a
  written date outside the plausible window.

Pure functions, no I/O or network access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

_MONTHS = {
    name: number
    for number, names in enumerate((
        ("jan", "january"), ("feb", "february"), ("mar", "march"),
        ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
        ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
        ("nov", "november"), ("dec", "december"),
    ), start=1)
    for name in names
}
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
          r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_ORD = r"(?:st|nd|rd|th)?"
# A four-digit year may run straight into the next date ("4/22/20266/20/2026").
_YEAR = r"(\d{4}(?=\d{1,2}\s*/)|\d+)"
_PATTERNS = (
    # (kind, regex). Earlier kinds win overlapping spans.
    ("ymd", re.compile(
        r"(?<![\d/-])(\d{4})(?:-(\d{1,2})-|\s*/\s*(\d{1,2})\s*/\s*)(\d{1,2})(?!\d)")),
    ("mdy", re.compile(rf"(?<!\d)(\d{{1,2}})\s*([/.-])\2*\s*(\d{{1,2}})\s*\2+\s*{_YEAR}")),
    ("dmy_named", re.compile(
        rf"(?<!\d)(\d{{1,2}}){_ORD}(?:\s*[-–]\s*\d{{1,2}}{_ORD})?\s+(?:of\s+)?({_MONTH})\.?,?\s+(\d{{4}})\b",
        re.I)),
    ("md_named", re.compile(
        rf"\b({_MONTH})\.?,?\s*(\d{{1,2}}){_ORD}(?!\d)(?:(?:\s*[-–]\s*\d{{1,2}}{_ORD})?[,.]?\s*(\d{{4}})\b)?",
        re.I)),
    ("my", re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{4})(?!\d)")),
    ("my_named", re.compile(
        rf"\b({_MONTH})\.?(?:\s*(?:-|–|—|to|through|thru|and|&|/)\s*{_MONTH}\.?)?,?\s+(?:of\s+)?(\d{{4}})\b",
        re.I)),
    ("md", re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?!\s*/*\s*\d)")),
)
# Anything that starts like a date. One not covered by a readable token is
# an unreadable date ("5/4/204", "04/-9/2020") and blocks later tokens from
# standing in for it.
_LOOSE = re.compile(rf"\d+\s*/+\s*-?\d+|\b{_MONTH}\b", re.I)
# Words that may precede a start date without changing its role.
_START_PREFIX = re.compile(
    r"^(?:(?:early|mid|late|beginning|begins|starting|commencing|on)\b[-:\s]*)?", re.I)
_EXCEL_SERIAL = re.compile(r"^\s*(\d{5})\s*$")


@dataclass(frozen=True)
class Token:
    start: int
    value: date | None  # None: unreadable, or yearless with no year to borrow
    precision: str | None  # "day" | "month"


@dataclass(frozen=True)
class Decision:
    """How the engine treats one upstream correction."""

    action: str
    upstream: date | None
    used: date | None
    precision: str | None
    reason: str


def _year(text: str) -> int | None:
    if len(text) == 4:
        return int(text)
    if len(text) == 2:  # strptime %y convention
        year = int(text)
        return year + (1900 if year >= 69 else 2000)
    return None


def _make(y: int | None, m: int, d: int | None) -> tuple[date | None, bool]:
    if y is None:
        return None, False
    try:
        return date(y, m, d or 1), True
    except ValueError:
        return None, False


def date_tokens(text: str) -> list[Token]:
    """Dates written in a cell, in reading order.

    A yearless day ("June 17 - June 30, 2020", "3/16 - 12/13/2020") takes
    the year of the next dated token when that token's month is not
    earlier; otherwise it stays unresolved.
    """
    raw: list[tuple[int, int, int | None, int, int | None, bool]] = []
    spans: list[tuple[int, int]] = []

    def free(span):
        return not any(s < span[1] and span[0] < e for s, e in spans)

    serial = _EXCEL_SERIAL.match(text)
    if serial and 32874 <= int(serial.group(1)) <= 60000:  # 1990..2064
        day = date(1899, 12, 30) + timedelta(days=int(serial.group(1)))
        return [Token(serial.start(1), day, "day")]

    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if not free(m.span()):
                continue
            g = m.groups()
            if kind == "ymd":
                y, mo, d, dated = int(g[0]), int(g[1] or g[2]), int(g[3]), True
            elif kind == "mdy":
                y, mo, d, dated = _year(g[3]), int(g[0]), int(g[2]), True
                if y is None:
                    raw.append((m.start(), -1, None, 0, None, True))
                    spans.append(m.span())
                    continue
            elif kind == "dmy_named":
                y, mo, d, dated = int(g[2]), _MONTHS[g[1].lower()], int(g[0]), True
            elif kind == "md_named":
                y = int(g[2]) if g[2] else None
                mo, d, dated = _MONTHS[g[0].lower()], int(g[1]), bool(g[2])
            elif kind == "my":
                y, mo, d, dated = int(g[1]), int(g[0]), None, True
            elif kind == "my_named":
                y, mo, d, dated = int(g[1]), _MONTHS[g[0].lower()], None, True
            else:  # md
                y, mo, d, dated = None, int(g[0]), int(g[1]), False
            raw.append((m.start(), 0, y, mo, d, dated))
            spans.append(m.span())
    for m in _LOOSE.finditer(text):
        if free(m.span()):
            raw.append((m.start(), -1, None, 0, None, True))
            spans.append(m.span())
    raw.sort()

    tokens: list[Token] = []
    for i, (start, bad, y, mo, d, dated) in enumerate(raw):
        if bad:
            tokens.append(Token(start, None, None))
            continue
        if not dated:
            later = next((r for r in raw[i + 1:] if r[1] == 0 and r[5]), None)
            if later is not None and (later[3], later[4] or 0) >= (mo, d or 0):
                y = later[2]
            elif later is None:
                years = re.findall(r"(?<!\d)(\d{4})(?!\d)", text[start:])
                y = int(years[0]) if len(set(years)) == 1 else None
        value, ok = _make(y, mo, d)
        tokens.append(Token(start, value if ok else None,
                            ("day" if d else "month") if ok else None))
    return tokens


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"unexpected correction value {value!r}")


def classify(key: str, value, *, minimum_year: int, max_future_days: int,
             today: date) -> Decision:
    """Decide whether one correction is supported by its own source cell."""
    upstream = _as_date(value)
    text = str(key)
    if upstream is None:
        return Decision("keep_null", None, None, None, "upstream_null")
    if re.match(r"\s*https?://", text):
        return Decision("keep_linked_document", upstream, upstream, None,
                        "upstream_transcribed_from_linked_document")
    tokens = date_tokens(text)
    if not tokens:
        return Decision("null", upstream, None, None, "no_date_in_cell")
    first = tokens[0]
    readable = [t.value for t in tokens if t.value is not None]
    for token in tokens:
        if token.value == upstream:
            if token is first:
                return Decision("keep", upstream, upstream, token.precision,
                                "date_written_in_cell")
            if upstream == min(readable):
                # A list written out of order; upstream took its earliest day.
                return Decision("keep", upstream, upstream, token.precision,
                                "upstream_chose_earliest_listed_date")
            if first.value is not None:
                # e.g. "November 9 through November 23, 2019" -> the end day.
                return Decision("first_literal", upstream, first.value,
                                first.precision, "upstream_chose_later_listed_date")
    if first.value is None:
        return Decision("null", upstream, None, None, "first_date_unreadable")
    stripped = text.lstrip()
    leading = len(text) - len(stripped)
    prefix = _START_PREFIX.match(stripped).end()
    if first.start != leading + prefix:
        return Decision("null", upstream, None, None, "cell_does_not_begin_with_date")
    low = date(minimum_year, 1, 1)
    high = today + timedelta(days=max_future_days)
    if not (low <= first.value <= high):
        return Decision("null", upstream, None, None, "written_date_outside_plausible_window")
    return Decision("literal", upstream, first.value, first.precision,
                    "upstream_date_not_written_in_cell")


def audit(transformer_cls, today: date) -> dict[str, Decision]:
    return {
        key: classify(key, value, minimum_year=transformer_cls.minimum_year,
                      max_future_days=transformer_cls.max_future_days, today=today)
        for key, value in transformer_cls.date_corrections.items()
    }


def sanitized_corrections(transformer_cls, today: date) -> tuple[dict, dict[str, Decision]]:
    """Correction table to install on a transformer instance, plus decisions.

    Values are datetimes (or None), as upstream's transform_date expects.
    """
    decisions = audit(transformer_cls, today)
    table = {
        key: (datetime.combine(d.used, datetime.min.time()) if d.used else None)
        for key, d in decisions.items()
    }
    return table, decisions
