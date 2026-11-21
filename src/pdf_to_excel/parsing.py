"""Money and date parsing for the formats invoices actually use."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

CURRENCY_SYMBOLS = {
    "US$": "USD",
    "CA$": "CAD",
    "C$": "CAD",
    "AU$": "AUD",
    "A$": "AUD",
    "NZ$": "NZD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "$": "USD",
}
ISO_CODES = (
    "USD", "EUR", "GBP", "CAD", "AUD", "NZD", "CHF", "JPY", "SEK", "NOK", "DKK",
    "PLN", "INR", "MXN", "ZAR", "SGD", "HKD",
)  # fmt: skip

_SYM = r"(?:US\$|CA\$|C\$|AU\$|A\$|NZ\$|[$€£¥₹])"
_CODE = r"(?:" + "|".join(ISO_CODES) + r")"
_NUM = r"\d{1,3}(?:[,.'  ]\d{3})+(?:[.,]\d{1,4})?|\d+(?:[.,]\d{1,4})?"
MONEY_RE = re.compile(
    rf"(?P<neg>[-(])?\s*(?P<pre>{_SYM}|{_CODE})?\s*(?P<neg2>-)?(?P<num>{_NUM})\s*(?P<post>{_SYM}|{_CODE})?\)?"
)


def detect_decimal_comma(texts: list[str]) -> bool:
    """True when the document writes decimals with a comma (1.234,56)."""
    comma = sum(len(re.findall(r"\d,\d{2}(?!\d)", t)) for t in texts)
    dot = sum(len(re.findall(r"\d\.\d{2}(?!\d)", t)) for t in texts)
    return comma > dot


def parse_number(text: str, decimal_comma: bool = False) -> Decimal | None:
    s = text.strip().replace(" ", " ")
    if not re.fullmatch(_NUM, s):
        return None
    dec, thou = (",", ".") if decimal_comma else (".", ",")
    s = s.replace(" ", "").replace("'", "")
    if s.count(dec) > 1:
        return None
    if thou in s:
        head, _, tail = s.partition(dec)
        groups = head.split(thou)
        # "1,5" in a dot-decimal document is not a thousands group: refuse it
        if any(len(g) != 3 for g in groups[1:]) or not 1 <= len(groups[0]) <= 3:
            return None
        s = "".join(groups) + (dec + tail if tail else "")
    try:
        return Decimal(s.replace(dec, "."))
    except InvalidOperation:
        return None


@dataclass
class Money:
    amount: Decimal
    currency: str | None
    has_decimals: bool


def parse_money(text: str, decimal_comma: bool = False) -> Money | None:
    """Parse a cell that holds exactly one amount, with an optional currency mark."""
    s = text.strip()
    if not s or "%" in s:
        return None
    m = MONEY_RE.fullmatch(s)
    if not m:
        return None
    amount = parse_number(m["num"], decimal_comma)
    if amount is None:
        return None
    if m["neg"] or m["neg2"]:
        amount = -amount
    mark = m["pre"] or m["post"]
    dec = "," if decimal_comma else "."
    return Money(
        amount=amount,
        currency=CURRENCY_SYMBOLS.get(mark, mark) if mark else None,
        has_decimals=bool(re.search(rf"\{dec}\d{{2}}$", m["num"])),
    )


MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}  # fmt: skip
_MON = r"(?P<mon>[A-Za-z]{3,9})\.?"
_DATE_PATTERNS = [
    ("iso", re.compile(r"(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})")),
    (
        "dmy_text",
        re.compile(rf"(?P<d>\d{{1,2}})(?:st|nd|rd|th)?[\s-]+{_MON}[\s,-]+(?P<y>\d{{4}}|\d{{2}})"),
    ),
    ("mdy_text", re.compile(rf"{_MON}\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>\d{{4}})")),
    (
        "numeric",
        re.compile(r"(?P<a>\d{1,2})(?P<sep>[/.-])(?P<b>\d{1,2})(?P=sep)(?P<y>\d{4}|\d{2})"),
    ),
]


@dataclass
class ParsedDate:
    value: date
    ambiguous: bool  # day and month could be swapped
    alt: date | None  # the other reading when ambiguous
    style: str
    two_digit_year: bool = False


def _mk(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text: str, day_first: bool | None = None) -> ParsedDate | None:
    """Parse a date at the start of ``text``.

    ``day_first`` settles 03/04/2026; when None the US reading is returned
    and the result is marked ambiguous so the caller can lower confidence.
    """
    s = text.strip()
    for style, rx in _DATE_PATTERNS:
        m = rx.match(s)
        if not m or (m.end() < len(s) and s[m.end()].isdigit()):
            continue
        short_year = len(m["y"]) == 2
        if style in ("dmy_text", "mdy_text"):
            mon = MONTHS.get(m["mon"].lower())
            if not mon:
                continue
            d = _mk(int(m["y"]), mon, int(m["d"]))
            if d:
                return ParsedDate(d, False, None, style, short_year)
            continue
        if style == "iso":
            d = _mk(int(m["y"]), int(m["m"]), int(m["d"]))
            if d:
                return ParsedDate(d, False, None, style)
            continue
        a, b, y = int(m["a"]), int(m["b"]), int(m["y"])
        mdy, dmy = _mk(y, a, b), _mk(y, b, a)
        # 12.03.2026 with dots is the continental convention; slashes and dashes are not settled
        if m["sep"] == "." and dmy and day_first is None:
            return ParsedDate(dmy, False, None, "numeric_dot", short_year)
        if mdy and dmy and mdy != dmy:
            if day_first is None:
                return ParsedDate(mdy, True, dmy, "numeric", short_year)
            first, other = (dmy, mdy) if day_first else (mdy, dmy)
            return ParsedDate(first, False, other, "numeric", short_year)
        only = mdy or dmy
        if only:
            return ParsedDate(only, False, None, "numeric", short_year)
    return None
