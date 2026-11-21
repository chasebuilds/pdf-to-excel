"""File triage, plausibility rules, batch checks and the audit sample."""

from datetime import date, timedelta
from decimal import Decimal

from openpyxl import load_workbook
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen.canvas import Canvas

from pdf_to_excel import DEFAULT_THRESHOLD, extract_invoice, write_workbook
from pdf_to_excel.extract import check_batch
from pdf_to_excel.workbook import pick_audit_sample, review_rows, status_of


def test_garbage_text_layer_is_reported_not_extracted(text_pdf):
    rows = [(50, 60 + 14 * i, "(cid:12)(cid:44)(cid:7) (cid:91)(cid:3)(cid:18)") for i in range(6)]
    r = extract_invoice(text_pdf(rows))
    assert status_of(r, DEFAULT_THRESHOLD) == "BAD TEXT LAYER"
    assert all(f.value is None for f in r.fields.values())
    assert "not readable text" in review_rows(r, DEFAULT_THRESHOLD)[0][6]


def test_symbol_soup_is_a_bad_text_layer(text_pdf):
    rows = [(50, 60 + 14 * i, "#### %%%% &&&& **** ]]]] ^^^^ ~~~~") for i in range(5)]
    assert extract_invoice(text_pdf(rows)).problem == "BAD TEXT LAYER"


def test_a_normal_invoice_is_not_a_bad_text_layer(invoice_pdf):
    path, _ = invoice_pdf("euro", 1)
    r = extract_invoice(path)
    assert r.problem == "" and not r.file_notes
    assert len(r.sha256) == 64 and r.decimal_comma is True


def test_password_protected_pdf_gets_its_own_status(tmp_path):
    path = tmp_path / "locked.pdf"
    c = Canvas(str(path), pagesize=LETTER, encrypt="secret")
    c.drawString(50, 700, "Invoice No: 123")
    c.save()
    r = extract_invoice(path)
    assert status_of(r, DEFAULT_THRESHOLD) == "ENCRYPTED"
    assert "unlocked copy" in review_rows(r, DEFAULT_THRESHOLD)[0][6]


def test_blank_page_pdf_is_no_text(tmp_path):
    path = tmp_path / "blank.pdf"
    c = Canvas(str(path), pagesize=LETTER)
    c.showPage()
    c.save()
    assert extract_invoice(path).problem == "NO TEXT"


def test_rotated_page_is_noted_in_review(tmp_path):
    path = tmp_path / "rotated.pdf"
    c = Canvas(str(path), pagesize=LETTER)
    c.setPageRotation(90)
    c.drawString(50, 700, "Harbor Tools LLC")
    c.drawString(50, 680, "Invoice No: 20417")
    c.save()
    r = extract_invoice(path)
    assert any("rotated" in note for note in r.file_notes)
    assert any(row[3] == "(file)" and "rotated" in row[6] for row in review_rows(r, 0.8))


def test_invoice_date_in_the_future_is_flagged(text_pdf):
    future = date.today() + timedelta(days=400)
    path = text_pdf([(50, 50, "Harbor Tools LLC"), (50, 90, f"Invoice Date: {future.isoformat()}")])
    r = extract_invoice(path)
    assert not next(c for c in r.checks if c.name == "invoice date is plausible").passed
    assert r.get("invoice_date").confidence < DEFAULT_THRESHOLD
    assert "in the future" in " ".join(r.get("invoice_date").reasons)


def test_tax_that_does_not_match_the_printed_rate_is_flagged(text_pdf):
    rows = [(50, 50, "Harbor Tools LLC"), (400, 190, "Subtotal"), (490, 190, "100.00")]
    rows += [(400, 206, "Tax (8%)"), (490, 206, "18.00"), (400, 222, "Total"), (490, 222, "118.00")]
    r = extract_invoice(text_pdf(rows))
    check = next(c for c in r.checks if c.name == "tax = subtotal x printed rate")
    assert not check.passed and "8%" in check.detail
    assert "is not 8% of subtotal" in " ".join(r.get("tax").reasons)


def test_tax_rate_check_passes_on_generated_invoices(invoice_pdf):
    path, _ = invoice_pdf("classic", 2)
    r = extract_invoice(path)
    assert next(c for c in r.checks if c.name == "tax = subtotal x printed rate").passed


def test_duplicate_invoice_number_from_one_vendor_is_flagged(invoice_pdf, tmp_path):
    path, _ = invoice_pdf("minimal", 1)
    copy = tmp_path / "copy_of_it.pdf"
    copy.write_bytes(path.read_bytes())
    other, _ = invoice_pdf("minimal", 2)
    a, b, c = check_batch([extract_invoice(p) for p in (path, copy, other)])
    for dup, twin in ((a, copy.name), (b, path.name)):
        assert dup.get("invoice_number").confidence < DEFAULT_THRESHOLD
        assert f"same vendor and invoice number as {twin}" in " ".join(
            dup.get("invoice_number").reasons
        )
    assert c.get("invoice_number").confidence >= DEFAULT_THRESHOLD
    assert a.sha256 == b.sha256 != c.sha256


def test_audit_sample_is_seeded_and_lands_on_the_review_sheet(invoice_pdf, tmp_path):
    results = [extract_invoice(invoice_pdf("minimal", s)[0]) for s in range(1, 7)]
    assert all(status_of(r, DEFAULT_THRESHOLD) == "OK" for r in results)
    first = [r.file for r in pick_audit_sample(results, DEFAULT_THRESHOLD, size=2)]
    assert first == [r.file for r in pick_audit_sample(results, DEFAULT_THRESHOLD, size=2)]
    assert len(pick_audit_sample(results, DEFAULT_THRESHOLD)) == 1  # 5 percent, at least one

    wb = load_workbook(write_workbook(results, tmp_path / "a.xlsx", audit_sample=2))
    audit = [
        r for r in wb["Review"].iter_rows(min_row=2, values_only=True) if r[3] == "(audit sample)"
    ]
    assert sorted(r[0] for r in audit) == sorted(first)
    summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True) if r[0]}
    assert summary["Audit sample: invoices picked for a spot check"] == 2
    assert summary["Sum of totals, GBP"] > 0

    off = load_workbook(write_workbook(results, tmp_path / "b.xlsx", audit_sample=0))
    assert not [r for r in off["Review"].iter_rows(min_row=2, values_only=True)]


def test_summary_has_totals_per_currency_and_a_file_manifest(invoice_pdf, tmp_path):
    results = [extract_invoice(invoice_pdf(layout, 1)[0]) for layout in ("minimal", "euro")]
    wb = load_workbook(write_workbook(results, tmp_path / "s.xlsx"))
    rows = list(wb["Summary"].iter_rows(values_only=True))
    summary = {r[0]: r[1] for r in rows if r[0]}
    assert summary["Sum of totals, GBP"] == float(results[0].get("total").value)
    assert summary["Sum of totals, EUR"] == float(results[1].get("total").value)
    manifest = [r for r in rows if r[0] == results[1].file and r[1] == results[1].sha256]
    assert manifest and manifest[0][4] == "1.234,56"
    assert results[1].get("total").value > Decimal(0)
