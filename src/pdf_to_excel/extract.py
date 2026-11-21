"""Turn one PDF into an InvoiceResult: fields, line items, checks."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal
from itertools import product
from pathlib import Path

from .fields import Candidate, FieldFinder, below_cells, right_cells
from .lineitems import extract_line_items
from .models import SCALAR_FIELDS, Check, Field, InvoiceResult, LineItem
from .parsing import detect_decimal_comma, parse_money
from .reader import read_pdf

CENT = Decimal("0.005")
Scored = tuple[float, Decimal, Candidate]


def _money_candidates(finder: FieldFinder, name: str) -> list[Scored]:
    out: list[Scored] = []
    for m, ms in finder.matches(name):
        parsed = [(c, parse_money(c.text, finder.decimal_comma)) for c in right_cells(m, ms)]
        parsed = [(c, v) for c, v in parsed if v]
        if parsed:
            cell, money = parsed[-1]  # amounts sit at the right end of a totals row
            cand, pos = Candidate(cell, 1.0, "right", m), 1.0
        else:
            under = [
                (c, s, v)
                for c, s in below_cells(m, ms, finder.page_of[m.line.index], finder.labels_by_line)
                if (v := parse_money(c.text, finder.decimal_comma))
            ]
            if not under:
                continue
            cell, pos, money = under[0]
            cand = Candidate(cell, pos, "below", m)
        shape = 1.0 if money.has_decimals else 0.9
        out.append((round(0.97 * m.strength * pos * shape, 3), money.amount, cand))
    # equal scores: the later one wins, totals are at the bottom of the document
    return sorted(out, key=lambda s: (-s[0], -s[2].match.line.index))


def _field_from(scored: Scored | None, what: str) -> Field:
    if scored is None:
        return Field(reasons=[f"no {what} label with an amount"])
    score, amount, cand = scored
    f = Field(
        value=amount,
        confidence=score,
        page=cand.match.line.page,
        method=f"label '{cand.match.text}' ({cand.how})",
        raw=cand.cell.text,
    )
    if cand.match.strength < 0.9:
        f.reasons.append(f"weak label '{cand.match.text}'")
    return f


def _totals(finder: FieldFinder, items: list[LineItem], result: InvoiceResult) -> None:
    subs = _money_candidates(finder, "subtotal")[:4]
    taxes = _money_candidates(finder, "tax")[:4]
    tots = _money_candidates(finder, "total")[:4]
    adjustments = Decimal(0)
    adj_names: list[str] = []
    for _, amount, cand in _money_candidates(finder, "adjustment"):
        if not cand.match.starts_cell:
            continue
        adjustments += -abs(amount) if "discount" in cand.match.text else amount
        adj_names.append(cand.match.text)

    pick = (subs[0] if subs else None, taxes[0] if taxes else None, tots[0] if tots else None)
    if subs and taxes and tots:
        # several labels can carry an amount ("Total", "Balance Due"): prefer the set that adds up
        for s, t, g in sorted(
            product(subs, taxes, tots), key=lambda c: -(c[0][0] + c[1][0] + c[2][0])
        ):
            if abs(s[1] + t[1] + adjustments - g[1]) <= CENT:
                pick = (s, t, g)
                break
    sub, tax, tot = (
        _field_from(pick[0], "subtotal"),
        _field_from(pick[1], "tax"),
        _field_from(pick[2], "total"),
    )
    line_sum = sum((i.amount for i in items if i.amount is not None), Decimal(0)) if items else None

    # Row arithmetic
    checkable = [i for i in items if i.quantity is not None and i.unit_price is not None]
    if checkable:
        bad = [i for i in checkable if abs(i.quantity * i.unit_price - i.amount) > Decimal("0.011")]
        result.checks.append(
            Check(
                "line amount = quantity x unit price",
                not bad,
                f"{len(bad)} of {len(checkable)} rows fail" if bad else f"{len(checkable)} rows",
            )
        )

    # Lines against subtotal
    if line_sum is not None and sub.value is not None:
        ok = abs(line_sum - sub.value) <= CENT
        result.checks.append(
            Check("line items sum to subtotal", ok, f"lines {line_sum}, subtotal {sub.value}")
        )
        if ok:
            sub.raise_to(0.97)
            for i in items:
                if i.confidence >= 0.8:
                    i.confidence = min(0.99, i.confidence + 0.02)
        else:
            reason = f"line items sum to {line_sum}, subtotal says {sub.value}"
            sub.lower(0.75, reason)
            result.line_items_reasons.append(reason)
            for i in items:
                i.confidence = round(i.confidence * 0.8, 3)

    # Subtotal + tax = total
    if sub.value is not None and tax.value is not None and tot.value is not None:
        ok = abs(sub.value + tax.value + adjustments - tot.value) <= CENT
        extra = f" + {', '.join(adj_names)} {adjustments}" if adj_names else ""
        result.checks.append(
            Check(
                "subtotal + tax = total",
                ok,
                f"{sub.value} + {tax.value}{extra} vs total {tot.value}",
            )
        )
        for f in (sub, tax, tot):
            if ok:
                f.raise_to(0.96)
            else:
                f.lower(
                    0.6,
                    f"subtotal + tax{extra} does not equal total "
                    f"({sub.value} + {tax.value} vs {tot.value})",
                )
    elif tot.value is None and sub.value is not None and tax.value is not None:
        tot = Field(
            value=sub.value + tax.value + adjustments,
            confidence=0.6,
            page=sub.page,
            method="derived",
            reasons=["no total label found; derived as subtotal + tax"],
        )
    elif (
        sub.value is None
        and tot.value is not None
        and tax.value is not None
        and line_sum is not None
    ):
        if abs(line_sum + tax.value + adjustments - tot.value) <= CENT:
            sub = Field(
                value=line_sum,
                confidence=0.88,
                page=tot.page,
                method="derived: line items sum = total - tax",
            )
            tax.raise_to(0.9)
            tot.raise_to(0.9)
            result.checks.append(
                Check("line items + tax = total", True, f"{line_sum} + {tax.value}")
            )
    elif (
        tax.value is None
        and sub.value is not None
        and tot.value is not None
        and tot.value >= sub.value
    ):
        tax = Field(
            value=tot.value - sub.value,
            confidence=0.6,
            page=tot.page,
            method="derived",
            reasons=["no tax label found; derived as total - subtotal"],
        )
    if not items and line_sum is None and sub.value is not None:
        result.checks.append(Check("line items sum to subtotal", False, "no line items found"))

    if pick[1] is not None and tax.method != "derived":
        _tax_rate_check(result, sub, tax, pick[1][2].match.line.text)
    result.fields.update(subtotal=sub, tax=tax, total=tot)


def _sanity_checks(result: InvoiceResult) -> None:
    """Plausibility rules that do not depend on layout."""
    issued, total = result.get("invoice_date"), result.get("total")
    if isinstance(issued.value, date):
        today = date.today()
        if issued.value > today:
            result.checks.append(
                Check("invoice date is plausible", False, f"{issued.value} is in the future")
            )
            issued.lower(0.7, "invoice date is in the future")
        elif issued.value.year < today.year - 10:
            result.checks.append(
                Check("invoice date is plausible", False, f"{issued.value} is over ten years old")
            )
            issued.lower(0.7, "invoice date is more than ten years old")
        else:
            result.checks.append(Check("invoice date is plausible", True, str(issued.value)))
    if total.value is not None and total.value == 0:
        result.checks.append(Check("total is not zero", False, "total reads 0.00"))
        total.lower(0.7, "total is zero")


def _tax_rate_check(result: InvoiceResult, sub: Field, tax: Field, line_text: str) -> None:
    """When the tax row prints a rate ("VAT 20 %"), tax should equal subtotal x rate."""
    m = re.search(r"(\d{1,2}(?:[.,]\d{1,3})?)\s*%", line_text)
    if not m or sub.value is None or tax.value is None:
        return
    rate = Decimal(m[1].replace(",", "."))
    expected = sub.value * rate / 100
    ok = abs(expected - tax.value) <= Decimal("0.02")
    result.checks.append(
        Check(
            "tax = subtotal x printed rate",
            ok,
            f"{sub.value} x {rate}% = {expected:.2f}, tax {tax.value}",
        )
    )
    if not ok:
        tax.lower(0.85, f"tax {tax.value} is not {rate}% of subtotal {sub.value}")


def extract_invoice(path: str | Path) -> InvoiceResult:
    path = Path(path)
    result = InvoiceResult(file=path.name)

    def stop(problem: str, detail: str) -> InvoiceResult:
        result.problem, result.problem_detail = problem, detail
        result.fields = {n: Field(reasons=[detail]) for n in SCALAR_FIELDS}
        return result

    try:
        result.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        doc = read_pdf(path)
    except Exception as exc:  # a broken PDF must not stop a batch
        if "password" in f"{type(exc).__name__} {exc!r}".lower():
            return stop("ENCRYPTED", "Password-protected PDF: send an unlocked copy.")
        result.error = f"could not read PDF: {type(exc).__name__}: {exc}"
        return stop("ERROR", result.error)
    result.page_count = len(doc.pages)
    result.scanned_pages = doc.scanned_pages
    if not doc.pages:
        return stop("EMPTY", "The PDF has no pages.")
    if not doc.has_text:
        if doc.scanned_pages:
            result.needs_ocr = True
            return stop(
                "NEEDS OCR", "Scanned image with no text layer: needs OCR. Nothing was extracted."
            )
        return stop("NO TEXT", "The PDF has no text and no page image. Nothing to extract.")
    text_pages = [p.number for p in doc.pages if p.lines]
    if doc.bad_text_pages and set(doc.bad_text_pages) == set(text_pages):
        return stop(
            "BAD TEXT LAYER",
            "The text inside this PDF is not readable text (broken font encoding or a bad "
            "hidden OCR layer). Nothing was extracted: it needs OCR or a fresh export.",
        )
    if doc.bad_text_pages:
        pages = ", ".join(map(str, doc.bad_text_pages))
        result.file_notes.append(
            f"page(s) {pages} have an unreadable text layer and may be misread"
        )
    if doc.rotated_pages:
        pages = ", ".join(map(str, doc.rotated_pages))
        result.file_notes.append(f"page(s) {pages} are rotated: reading order may be wrong")

    decimal_comma = detect_decimal_comma([ln.text for ln in doc.lines])
    result.decimal_comma = decimal_comma
    table = extract_line_items(doc, decimal_comma)
    result.line_items = table.items
    result.line_items_reasons.extend(table.reasons)

    finder = FieldFinder(doc, table.used_lines, decimal_comma)
    result.fields["invoice_number"] = finder.invoice_number()
    result.fields["invoice_date"], result.fields["due_date"] = finder.dates()
    result.fields["bill_to"] = finder.bill_to()
    result.fields["vendor"] = finder.vendor(
        result.fields["bill_to"].value, min(table.used_lines, default=None)
    )
    result.fields["currency"] = finder.currency()
    _totals(finder, table.items, result)
    _sanity_checks(result)

    if result.scanned_pages:
        pages = ", ".join(map(str, result.scanned_pages))
        result.line_items_reasons.append(f"page(s) {pages} are scanned images and were not read")
        for name in ("subtotal", "tax", "total"):
            result.fields[name].lower(0.7, f"page(s) {pages} are scanned images and were not read")
    for f in result.fields.values():
        f.confidence = round(min(max(f.confidence, 0.0), 0.99), 3)
    return result


def check_batch(results: list[InvoiceResult]) -> list[InvoiceResult]:
    """Checks that need the whole batch: the same invoice number twice from one vendor."""
    seen: dict[tuple[str, str], list[InvoiceResult]] = {}
    for r in results:
        vendor, number = r.get("vendor").value, r.get("invoice_number").value
        if vendor and number:
            seen.setdefault((str(vendor).casefold(), str(number).casefold()), []).append(r)
    for group in seen.values():
        if len(group) < 2:
            continue
        for r in group:
            others = ", ".join(o.file for o in group if o is not r)
            r.checks.append(
                Check("invoice number is unique for this vendor", False, f"also in {others}")
            )
            r.fields["invoice_number"].lower(
                0.7, f"same vendor and invoice number as {others}: possible duplicate"
            )
    return results


def extract_folder(folder: str | Path) -> list[InvoiceResult]:
    folder = Path(folder)
    files = [folder] if folder.is_file() else sorted(folder.glob("*.pdf"))
    return check_batch([extract_invoice(p) for p in files])
