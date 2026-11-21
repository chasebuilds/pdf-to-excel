"""The HTTP service, driven through FastAPI's TestClient with the repo's sample PDFs."""

from io import BytesIO
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from pdf_to_excel import __version__  # noqa: E402
from pdf_to_excel.api import XLSX_TYPE, app  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
client = TestClient(app)


def post(path: str, sample: Path, name: str | None = None, **params):
    with sample.open("rb") as fh:
        files = {"file": (name or sample.name, fh, "application/pdf")}
        return client.post(path, files=files, params=params)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": __version__}


def test_extract_clean_invoice():
    r = post("/extract", SAMPLES / "invoices" / "01_classic_us.pdf")
    assert r.status_code == 200
    body = r.json()
    assert body["file"] == "01_classic_us.pdf"
    assert body["status"] == "OK"
    assert len(body["sha256"]) == 64
    assert set(body["fields"]) >= {"vendor", "invoice_number", "invoice_date", "total"}
    for f in body["fields"].values():
        assert 0.0 <= f["confidence"] <= 1.0
    assert body["fields"]["currency"]["value"] == "USD"
    assert len(body["line_items"]) == 10
    assert body["review"] == []


def test_extract_matches_the_library_result():
    from pdf_to_excel import extract_invoice

    sample = SAMPLES / "invoices" / "02_euro_comma_decimals.pdf"
    lib = extract_invoice(sample)
    body = post("/extract", sample).json()
    assert body["fields"]["total"]["value"] == str(lib.get("total").value) == "117514.63"
    assert body["fields"]["invoice_date"]["value"] == "2026-03-29"
    assert body["fields"]["total"]["confidence"] == lib.get("total").confidence


def test_bad_arithmetic_is_listed_for_review():
    body = post("/extract", SAMPLES / "invoices" / "08_total_does_not_add_up.pdf").json()
    assert body["status"] == "REVIEW"
    total = [x for x in body["review"] if x["field"] == "total"]
    assert total and "does not equal total" in total[0]["reason"]
    assert total[0]["confidence"] < 0.8


def test_threshold_changes_what_goes_to_review():
    sample = SAMPLES / "invoices" / "01_classic_us.pdf"
    strict = post("/extract", sample, threshold=1.0).json()
    assert strict["status"] == "REVIEW" and strict["review"]
    assert post("/extract", sample, threshold=1.5).status_code == 422


def test_tilted_page_note_reaches_the_review_list():
    body = post("/extract", SAMPLES / "skewed" / "tilt_3deg.pdf").json()
    notes = [x["reason"] for x in body["review"] if x["field"] == "(file)"]
    assert any("straightened" in n for n in notes)


def test_image_only_pdf_is_422():
    r = post("/extract", SAMPLES / "invoices" / "09_scanned_image_only.pdf")
    assert r.status_code == 422
    assert r.json()["detail"]["status"] == "NEEDS OCR"


def test_non_pdf_is_422():
    files = {"file": ("notes.pdf", BytesIO(b"just some text, not a PDF"), "application/pdf")}
    r = client.post("/extract", files=files)
    assert r.status_code == 422
    assert "not a PDF" in r.json()["detail"]


def test_missing_file_is_422():
    assert client.post("/extract").status_code == 422


def test_truncated_pdf_is_422():
    data = (SAMPLES / "invoices" / "01_classic_us.pdf").read_bytes()[:400]
    files = {"file": ("broken.pdf", BytesIO(data), "application/pdf")}
    r = client.post("/extract", files=files)
    assert r.status_code == 422
    assert r.json()["detail"]["status"] == "ERROR"


def test_upload_name_cannot_escape_the_temp_folder():
    r = post("/extract", SAMPLES / "invoices" / "01_classic_us.pdf", name="../../evil.pdf")
    assert r.status_code == 200
    assert r.json()["file"] == "evil.pdf"


def test_extract_xlsx_returns_a_workbook():
    r = post("/extract.xlsx", SAMPLES / "invoices" / "08_total_does_not_add_up.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == XLSX_TYPE
    assert 'filename="08_total_does_not_add_up.xlsx"' in r.headers["content-disposition"]
    wb = load_workbook(BytesIO(r.content))
    assert wb.sheetnames == ["Invoices", "LineItems", "Review", "Summary"]
    assert wb["Invoices"].max_row == 2


def test_extract_xlsx_rejects_image_only_pdf():
    r = post("/extract.xlsx", SAMPLES / "invoices" / "09_scanned_image_only.pdf")
    assert r.status_code == 422
