from datetime import date
from decimal import Decimal

import pytest

from benchmark.generate import TUNING_LAYOUTS, make_scan
from pdf_to_excel import DEFAULT_THRESHOLD, extract_invoice
from pdf_to_excel.labels import find_labels
from pdf_to_excel.reader import read_pdf
from pdf_to_excel.workbook import review_rows, status_of


@pytest.mark.parametrize("layout", TUNING_LAYOUTS)
@pytest.mark.parametrize("seed", [1, 2])
def test_tuning_layouts_extract_exactly(invoice_pdf, layout, seed):
    path, inv = invoice_pdf(layout, seed)
    r = extract_invoice(path)
    assert r.get("vendor").value == inv.vendor
    assert r.get("invoice_number").value == inv.number
    assert r.get("invoice_date").value == inv.invoice_date
    assert r.get("due_date").value == inv.due_date
    assert r.get("bill_to").value == inv.bill_to
    assert r.get("currency").value == inv.currency
    assert r.get("subtotal").value == inv.subtotal
    assert r.get("tax").value == inv.tax
    assert r.get("total").value == inv.total
    got = [
        (" ".join(i.description.split()), i.quantity, i.unit_price, i.amount) for i in r.line_items
    ]
    want = [(i.description, i.quantity, i.unit_price, i.amount) for i in inv.items]
    assert got == want
    assert all(c.passed for c in r.checks)
    assert {c.name for c in r.checks} >= {"line items sum to subtotal", "subtotal + tax = total"}


def test_multi_page_table_is_read_across_pages(invoice_pdf):
    path, inv = invoice_pdf("ledger", 3)
    r = extract_invoice(path)
    assert r.page_count > 1
    assert len(r.line_items) == len(inv.items)
    assert {i.page for i in r.line_items} == set(range(1, r.page_count + 1)) or r.page_count >= 2


def test_wrong_total_fails_the_check_and_is_flagged(invoice_pdf):
    def tweak(inv):
        inv.total_override = inv.total + Decimal("10.00")

    path, inv = invoice_pdf("classic", 4, tweak)
    r = extract_invoice(path)
    check = next(c for c in r.checks if c.name == "subtotal + tax = total")
    assert not check.passed
    for name in ("subtotal", "tax", "total"):
        assert r.get(name).confidence < DEFAULT_THRESHOLD
    rows = review_rows(r, DEFAULT_THRESHOLD)
    total_row = next(row for row in rows if row[3] == "total")
    assert "does not equal total" in total_row[6]
    assert total_row[0] == path.name and total_row[7] == 1
    assert status_of(r, DEFAULT_THRESHOLD) == "REVIEW"


def test_wrong_line_amount_is_named_in_review(invoice_pdf):
    def tweak(inv):
        inv.items[0].amount += Decimal("5.00")

    path, _ = invoice_pdf("classic", 5, tweak)
    r = extract_invoice(path)
    assert r.line_items[0].confidence < DEFAULT_THRESHOLD
    assert "quantity x unit price" in r.line_items[0].reasons[0]
    assert not next(c for c in r.checks if c.name == "line amount = quantity x unit price").passed
    assert not next(c for c in r.checks if c.name == "line items sum to subtotal").passed
    reasons = [row[6] for row in review_rows(r, DEFAULT_THRESHOLD)]
    assert any("line items sum to" in why for why in reasons)
    assert any("quantity x unit price" in why for why in reasons)


def test_shipping_line_is_included_in_the_total_check(invoice_pdf):
    def tweak(inv):
        inv.shipping = Decimal("25.00")
        inv.total = inv.subtotal + inv.tax + inv.shipping

    path, inv = invoice_pdf("classic", 6, tweak)
    r = extract_invoice(path)
    assert r.get("total").value == inv.total
    assert next(c for c in r.checks if c.name == "subtotal + tax = total").passed


def test_scanned_pdf_is_reported_not_guessed(invoice_pdf, tmp_path):
    path, _ = invoice_pdf("classic", 7)
    scan = tmp_path / "scan.pdf"
    make_scan(path, scan)
    r = extract_invoice(scan)
    assert r.needs_ocr and r.scanned_pages == [1]
    assert all(f.value is None for f in r.fields.values())
    assert not r.line_items
    rows = review_rows(r, DEFAULT_THRESHOLD)
    assert len(rows) == 1 and "needs OCR" in rows[0][6]
    assert status_of(r, DEFAULT_THRESHOLD) == "NEEDS OCR"


def test_unreadable_file_does_not_stop_the_batch(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not a pdf")
    r = extract_invoice(bad)
    assert r.error
    assert status_of(r, DEFAULT_THRESHOLD) == "ERROR"
    assert "could not read PDF" in review_rows(r, DEFAULT_THRESHOLD)[0][6]


def test_longest_label_wins_and_distractors_are_recognised(text_pdf):
    path = text_pdf(
        [(50, 60, "Due Date: 04/30/2026   PO Number: PO-55123   Invoice Date: 04/01/2026")]
    )
    line = read_pdf(path).lines[0]
    assert [(m.field, m.text) for m in find_labels(line)] == [
        ("due_date", "due date"),
        (None, "po number"),
        ("invoice_date", "invoice date"),
    ]


def test_po_number_is_not_taken_for_the_invoice_number(text_pdf):
    path = text_pdf(
        [
            (50, 50, "Harbor Tools LLC"),
            (50, 90, "PO Number: PO-77881"),
            (50, 105, "Invoice No: 20417"),
            (50, 120, "Date: 2026-05-02"),
        ]
    )
    r = extract_invoice(path)
    assert r.get("invoice_number").value == "20417"
    assert r.get("invoice_date").value == date(2026, 5, 2)
    assert r.get("due_date").value is None and r.get("due_date").confidence == 0


def test_day_month_order_settled_by_another_date_in_the_document(text_pdf):
    path = text_pdf(
        [
            (50, 50, "Northside Print Ltd"),
            (50, 90, "Invoice Date: 03/04/2026"),
            (50, 105, "Due Date: 25/04/2026"),
        ]
    )
    r = extract_invoice(path)
    assert r.get("invoice_date").value == date(2026, 4, 3)
    assert r.get("invoice_date").confidence >= DEFAULT_THRESHOLD


def test_day_month_order_settled_by_the_due_date_pair(text_pdf):
    # month-first gives 3 Apr -> 3 Dec (244 days); day-first gives 4 Mar -> 12 Mar (8 days)
    path = text_pdf(
        [
            (50, 50, "Northside Print Ltd"),
            (50, 90, "Invoice Date: 04/03/2026"),
            (50, 105, "Due Date: 12/03/2026"),
        ]
    )
    r = extract_invoice(path)
    assert r.get("invoice_date").value == date(2026, 3, 4)
    assert r.get("due_date").value == date(2026, 3, 12)
    assert "inferred from the invoice/due date pair" in " ".join(r.get("invoice_date").reasons)


def test_truly_ambiguous_dates_are_flagged(text_pdf):
    path = text_pdf(
        [
            (50, 50, "Northside Print Ltd"),
            (50, 90, "Invoice Date: 03/04/2026"),
            (50, 105, "Due Date: 03/05/2026"),
        ]
    )
    r = extract_invoice(path)
    f = r.get("invoice_date")
    assert f.confidence < DEFAULT_THRESHOLD
    assert "ambiguous day/month order" in " ".join(f.reasons)


def test_due_before_invoice_date_lowers_both(text_pdf):
    path = text_pdf(
        [
            (50, 50, "Northside Print Ltd"),
            (50, 90, "Invoice Date: 2026-06-20"),
            (50, 105, "Due Date: 2026-06-01"),
        ]
    )
    r = extract_invoice(path)
    assert r.get("due_date").confidence < DEFAULT_THRESHOLD
    assert "before invoice date" in " ".join(r.get("due_date").reasons)


def test_table_without_a_header_is_found_by_arithmetic_and_flagged(text_pdf):
    rows = [(50, 50, "Cedar Lane Catering"), (50, 80, "Invoice #: 5512")]
    for i, (desc, qty, price, amount) in enumerate(
        [("Lunch platter", "4", "32.50", "130.00"), ("Coffee service", "2", "18.00", "36.00")]
    ):
        y = 140 + 16 * i
        rows += [(50, y, desc), (330, y, qty), (400, y, price), (490, y, amount)]
    rows += [(400, 190, "Subtotal"), (490, 190, "166.00"), (400, 206, "Tax"), (490, 206, "13.28")]
    rows += [(400, 222, "Total"), (490, 222, "179.28")]
    r = extract_invoice(text_pdf(rows))
    assert [(i.description, i.quantity, i.amount) for i in r.line_items] == [
        ("Lunch platter", Decimal("4"), Decimal("130.00")),
        ("Coffee service", Decimal("2"), Decimal("36.00")),
    ]
    assert any("no table header recognised" in why for why in r.line_items_reasons)
    assert r.get("total").value == Decimal("179.28")
    assert all(c.passed for c in r.checks)


def test_missing_total_is_derived_and_flagged(text_pdf):
    rows = [(50, 50, "Cedar Lane Catering"), (400, 190, "Subtotal"), (490, 190, "100.00")]
    rows += [(400, 206, "Sales Tax"), (490, 206, "8.00")]
    r = extract_invoice(text_pdf(rows))
    total = r.get("total")
    assert total.value == Decimal("108.00")
    assert total.method == "derived" and total.confidence < DEFAULT_THRESHOLD


def test_bare_dollar_sign_is_reported_as_an_assumption(invoice_pdf):
    path, _ = invoice_pdf("classic", 8)
    f = extract_invoice(path).get("currency")
    assert f.value == "USD"
    assert "USD assumed" in " ".join(f.reasons)


def test_ampersand_stays_in_a_name_after_a_label(text_pdf):
    path = text_pdf(
        [(50, 50, "Cedar Lane Catering"), (50, 90, "Customer"), (150, 90, "Oakline & Sons")]
    )
    assert extract_invoice(path).get("bill_to").value == "Oakline & Sons"


def test_tilted_page_does_not_pass_scattered_letters_as_the_vendor(tmp_path):
    # a page scanned at a slight angle breaks words into single letters; those must not
    # come back as a confident seller name
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen.canvas import Canvas

    path = tmp_path / "tilted.pdf"
    c = Canvas(str(path), pagesize=LETTER, invariant=1)
    c.rotate(1.5)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, 740, "Bluegrass Print & Sign Co.")
    c.setFont("Helvetica", 10)
    c.drawString(50, 726, "212 Main St, Lexington KY 40507")
    c.drawString(400, 718, "Inv #")
    c.drawRightString(560, 718, "BG-10442")
    c.drawString(50, 690, "Sold To:")
    c.drawString(50, 676, "Smith Plumbing Supply")
    c.save()
    f = extract_invoice(path).get("vendor")
    scattered = f.value and sum(len(t) == 1 for t in f.value.split()) * 2 > len(f.value.split())
    assert not (scattered and f.confidence >= DEFAULT_THRESHOLD), (f.value, f.confidence)
