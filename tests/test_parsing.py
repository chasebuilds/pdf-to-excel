from datetime import date
from decimal import Decimal

import pytest

from pdf_to_excel.parsing import detect_decimal_comma, parse_date, parse_money, parse_number


@pytest.mark.parametrize(
    ("text", "comma", "amount", "currency"),
    [
        ("$1,234.56", False, "1234.56", "USD"),
        ("1.234,56 €", True, "1234.56", "EUR"),
        ("€ 1.234,56", True, "1234.56", "EUR"),
        ("£12.00", False, "12.00", "GBP"),
        ("1,234.56 USD", False, "1234.56", "USD"),
        ("CAD 99.10", False, "99.10", "CAD"),
        ("1 234,56", True, "1234.56", None),
        ("CHF 1'234.56", False, "1234.56", "CHF"),
        ("$1234.5", False, "1234.5", "USD"),
        ("(45.00)", False, "-45.00", None),
        ("-45.00", False, "-45.00", None),
        ("1,234,567.89", False, "1234567.89", None),
    ],
)
def test_parse_money(text, comma, amount, currency):
    m = parse_money(text, comma)
    assert m is not None
    assert m.amount == Decimal(amount)
    assert m.currency == currency


@pytest.mark.parametrize("text", ["8%", "20 %", "INV-204", "Net 30", "", "12/03/2026", "1.2.3"])
def test_parse_money_rejects(text):
    assert parse_money(text) is None


def test_parse_number_refuses_a_decimal_comma_in_a_dot_document():
    assert parse_number("2,5", decimal_comma=False) is None
    assert parse_number("2,5", decimal_comma=True) == Decimal("2.5")
    assert parse_number("1,234", decimal_comma=False) == Decimal("1234")


def test_detect_decimal_comma():
    assert detect_decimal_comma(["Total 1.234,56", "Tax 12,00"])
    assert not detect_decimal_comma(["Total 1,234.56", "Tax 12.00"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-03-12", date(2026, 3, 12)),
        ("12.03.2026", date(2026, 3, 12)),
        ("12 March 2026", date(2026, 3, 12)),
        ("12th March 2026", date(2026, 3, 12)),
        ("March 12, 2026", date(2026, 3, 12)),
        ("Mar 12, 2026", date(2026, 3, 12)),
        ("12-Mar-2026", date(2026, 3, 12)),
        ("12 Mar 2026", date(2026, 3, 12)),
        ("Sept 3, 2026", date(2026, 9, 3)),
        ("25/03/2026", date(2026, 3, 25)),
        ("03/25/2026", date(2026, 3, 25)),
        ("03-25-26", date(2026, 3, 25)),
    ],
)
def test_parse_date_formats(text, expected):
    parsed = parse_date(text)
    assert parsed is not None
    assert parsed.value == expected
    assert not parsed.ambiguous


def test_ambiguous_date_keeps_both_readings():
    parsed = parse_date("03/04/2026")
    assert parsed.ambiguous
    assert parsed.value == date(2026, 3, 4)
    assert parsed.alt == date(2026, 4, 3)
    assert parse_date("03/04/2026", day_first=True).value == date(2026, 4, 3)
    assert not parse_date("03/04/2026", day_first=True).ambiguous


@pytest.mark.parametrize("text", ["Net 30", "31/31/2026", "INV-2026-0042", "hello", "2026"])
def test_parse_date_rejects(text):
    assert parse_date(text) is None
