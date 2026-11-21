"""Write results to an Excel workbook: Invoices, LineItems, Review, Summary."""

from __future__ import annotations

import math
import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from . import __version__
from .models import DEFAULT_THRESHOLD, SCALAR_FIELDS, InvoiceResult

YELLOW = PatternFill("solid", start_color="FFF2A8", end_color="FFF2A8")
HEAD_FILL = PatternFill("solid", start_color="1F2937", end_color="1F2937")
HEAD_FONT = Font(bold=True, color="FFFFFF")
MONEY_FIELDS = ("subtotal", "tax", "total")
DATE_FIELDS = ("invoice_date", "due_date")


def _xl(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def _header(ws: Worksheet, names: list[str]) -> None:
    ws.append(names)
    for cell in ws[1]:
        cell.fill, cell.font = HEAD_FILL, HEAD_FONT
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"


def _fit(ws: Worksheet, cap: int = 60) -> None:
    for i, col in enumerate(ws.columns, start=1):
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(i)].width = min(cap, max(10, width + 2))


def status_of(r: InvoiceResult, threshold: float) -> str:
    if r.problem:
        return r.problem
    low_field = any(r.get(n).confidence < threshold for n in SCALAR_FIELDS)
    low_line = any(i.confidence < threshold for i in r.line_items) or not r.line_items
    return "REVIEW" if low_field or low_line else "OK"


def review_rows(r: InvoiceResult, threshold: float) -> list[list[Any]]:
    """One row per doubtful thing: file, invoice, sheet, field, value, confidence, reason, page."""
    number = r.get("invoice_number").value or ""
    if r.problem:
        pages = ", ".join(map(str, r.scanned_pages))
        return [[r.file, "", "Invoices", "(file)", "", 0.0, r.problem_detail, pages]]
    rows: list[list[Any]] = [
        [r.file, number, "Invoices", "(file)", "", "", note, ""] for note in r.file_notes
    ]
    for name in SCALAR_FIELDS:
        f = r.get(name)
        if f.confidence < threshold:
            why = "; ".join(f.reasons) or "confidence below threshold"
            value = f.value.isoformat() if isinstance(f.value, date) else _xl(f.value)
            rows.append([r.file, number, "Invoices", name, value, f.confidence, why, f.page or ""])
    for why in r.line_items_reasons:
        rows.append([r.file, number, "LineItems", "(all lines)", "", "", why, ""])
    for n, item in enumerate(r.line_items, start=1):
        if item.confidence < threshold and item.reasons:
            rows.append(
                [
                    r.file, number, "LineItems", f"line {n}", item.description,
                    item.confidence, "; ".join(item.reasons), item.page or "",
                ]
            )  # fmt: skip
    return rows


def pick_audit_sample(
    results: list[InvoiceResult], threshold: float, size: int | None = None, seed: int = 0
) -> list[InvoiceResult]:
    """A seeded random pick of invoices that passed, for a person to spot check.

    Confidence is a score, not a guarantee. Checking a few rows nobody flagged
    is how a client finds out whether the unflagged rows can be trusted.
    """
    passed = [r for r in results if status_of(r, threshold) == "OK"]
    if size is None:
        size = max(1, math.ceil(len(passed) * 0.05))
    return random.Random(seed).sample(passed, min(size, len(passed)))


def write_workbook(
    results: list[InvoiceResult],
    out_path: str | Path,
    threshold: float = DEFAULT_THRESHOLD,
    audit_sample: int | None = None,
) -> Path:
    wb = Workbook()

    inv = wb.active
    inv.title = "Invoices"
    conf_cols = [f"{n}_conf" for n in SCALAR_FIELDS]
    _header(
        inv, ["file", *SCALAR_FIELDS, "line_count", "pages", "status", "min_confidence", *conf_cols]
    )
    for r in results:
        confs = [r.get(n).confidence for n in SCALAR_FIELDS]
        inv.append(
            [
                r.file,
                *[_xl(r.get(n).value) for n in SCALAR_FIELDS],
                len(r.line_items),
                r.page_count,
                status_of(r, threshold),
                min(confs),
                *confs,
            ]
        )
        row = inv.max_row
        for col, name in enumerate(SCALAR_FIELDS, start=2):
            cell = inv.cell(row=row, column=col)
            if confs[col - 2] < threshold:
                cell.fill = YELLOW
            if name in MONEY_FIELDS:
                cell.number_format = "#,##0.00"
            elif name in DATE_FIELDS:
                cell.number_format = "yyyy-mm-dd"
        status = inv.cell(row=row, column=len(SCALAR_FIELDS) + 4)
        if status.value != "OK":
            status.fill = YELLOW
    _fit(inv)

    li = wb.create_sheet("LineItems")
    _header(
        li,
        [
            "file",
            "invoice_number",
            "line",
            "description",
            "quantity",
            "unit_price",
            "amount",
            "confidence",
            "page",
        ],
    )
    for r in results:
        for n, item in enumerate(r.line_items, start=1):
            li.append(
                [
                    r.file, r.get("invoice_number").value, n, item.description, _xl(item.quantity),
                    _xl(item.unit_price), _xl(item.amount), item.confidence, item.page,
                ]
            )  # fmt: skip
            row = li.max_row
            li.cell(row=row, column=6).number_format = "#,##0.00"
            li.cell(row=row, column=7).number_format = "#,##0.00"
            if item.confidence < threshold:
                for col in range(4, 8):
                    li.cell(row=row, column=col).fill = YELLOW
    _fit(li)

    rv = wb.create_sheet("Review")
    _header(
        rv,
        [
            "file",
            "invoice_number",
            "sheet",
            "field",
            "extracted_value",
            "confidence",
            "reason",
            "page",
        ],
    )
    for r in results:
        for row in review_rows(r, threshold):
            rv.append(row)
    audit = pick_audit_sample(results, threshold, audit_sample)
    for r in audit:
        why = (
            "Audit sample: this invoice passed every check and was picked at random. "
            "Compare its row with the PDF and note any error on the Summary sheet."
        )
        number = r.get("invoice_number").value
        rv.append([r.file, number, "Invoices", "(audit sample)", "", r.min_confidence(), why, ""])
    _fit(rv, cap=90)

    sm = wb.create_sheet("Summary")
    _header(sm, ["metric", "value"])
    statuses = [status_of(r, threshold) for r in results]
    n_fields = sum(1 for r in results if not r.problem for _ in SCALAR_FIELDS)
    by_currency: dict[str, float] = {}
    for r in results:
        if r.get("total").value is not None:
            code = r.get("currency").value or "unknown currency"
            by_currency[code] = by_currency.get(code, 0.0) + float(r.get("total").value)
    other = [s for s in statuses if s not in ("OK", "REVIEW", "NEEDS OCR", "ERROR")]
    low = sum(
        1
        for r in results
        if not r.problem
        for n in SCALAR_FIELDS
        if r.get(n).confidence < threshold
    )
    checks = [c for r in results for c in r.checks]
    for metric, value in [
        ("Files processed", len(results)),
        ("Status OK (nothing to review)", statuses.count("OK")),
        ("Status REVIEW", statuses.count("REVIEW")),
        ("Status NEEDS OCR (scanned, not read)", statuses.count("NEEDS OCR")),
        ("Status ERROR (unreadable file)", statuses.count("ERROR")),
        (
            "Other files not read (encrypted, empty, no text, bad text layer)",
            ", ".join(f"{s} {other.count(s)}" for s in sorted(set(other))) or 0,
        ),
        ("Header fields extracted", n_fields),
        ("Header fields below threshold (yellow)", low),
        ("Line items extracted", sum(len(r.line_items) for r in results)),
        (
            "Line items below threshold (yellow)",
            sum(1 for r in results for i in r.line_items if i.confidence < threshold),
        ),
        ("Checks run", len(checks)),
        ("Checks failed", sum(1 for c in checks if not c.passed)),
        *[(f"Sum of totals, {code}", round(v, 2)) for code, v in sorted(by_currency.items())],
        ("Audit sample: invoices picked for a spot check", len(audit)),
        ("Audit sample: errors found (fill in after checking)", None),
        ("Confidence threshold", threshold),
        ("Tool", f"pdf-to-excel {__version__}"),
    ]:
        sm.append([metric, value])
    sm.append([])
    sm.append(["Check", "File", "Result", "Detail"])
    for cell in sm[sm.max_row]:
        cell.font = Font(bold=True)
    for r in results:
        for c in r.checks:
            sm.append([c.name, r.file, "pass" if c.passed else "FAIL", c.detail])
            if not c.passed:
                sm.cell(row=sm.max_row, column=3).fill = YELLOW
    sm.append([])
    sm.append(["File", "SHA-256", "Pages", "Status", "Decimal style"])
    for cell in sm[sm.max_row]:
        cell.font = Font(bold=True)
    for r, status in zip(results, statuses, strict=True):
        style = {True: "1.234,56", False: "1,234.56", None: ""}[r.decimal_comma]
        sm.append([r.file, r.sha256, r.page_count, status, style])
    _fit(sm)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
