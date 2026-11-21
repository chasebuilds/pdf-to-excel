import json
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from benchmark.generate import make_scan
from pdf_to_excel import DEFAULT_THRESHOLD, extract_invoice, write_workbook
from pdf_to_excel.cli import main
from pdf_to_excel.llm import apply_llm_fallback
from pdf_to_excel.models import SCALAR_FIELDS

ROOT = Path(__file__).resolve().parent.parent
YELLOW = "00FFF2A8"


def _is_yellow(cell) -> bool:
    return cell.fill.fill_type == "solid" and cell.fill.start_color.rgb == YELLOW


@pytest.fixture
def batch(invoice_pdf, tmp_path):
    good, inv = invoice_pdf("minimal", 1)

    def tweak(i):
        i.total_override = i.total + Decimal("1.00")

    bad, _ = invoice_pdf("euro", 2, tweak)
    scan = tmp_path / "zz_scan.pdf"
    make_scan(good, scan)
    return [good, bad, scan], inv


def test_workbook_sheets_types_and_highlighting(batch, tmp_path):
    paths, inv = batch
    out = write_workbook([extract_invoice(p) for p in paths], tmp_path / "out.xlsx")
    wb = load_workbook(out)
    assert wb.sheetnames == ["Invoices", "LineItems", "Review", "Summary"]

    ws = wb["Invoices"]
    head = [c.value for c in ws[1]]
    assert head[:10] == ["file", *SCALAR_FIELDS]
    row = {h: c for h, c in zip(head, ws[2], strict=True)}
    assert row["vendor"].value == inv.vendor
    assert row["total"].value == pytest.approx(float(inv.total))
    assert isinstance(row["invoice_date"].value, datetime)
    assert row["status"].value == "OK"
    assert not any(_is_yellow(row[n]) for n in SCALAR_FIELDS)

    bad = {h: c for h, c in zip(head, ws[3], strict=True)}
    assert bad["status"].value == "REVIEW"
    assert _is_yellow(bad["total"]) and bad["total_conf"].value < DEFAULT_THRESHOLD
    assert {h: c for h, c in zip(head, ws[4], strict=True)}["status"].value == "NEEDS OCR"

    review = list(wb["Review"].iter_rows(min_row=2, values_only=True))
    assert any(
        r[0] == paths[1].name and r[3] == "total" and "does not equal total" in r[6] for r in review
    )
    assert any(r[0] == paths[2].name and "needs OCR" in r[6] for r in review)
    assert not any(r[0] == paths[0].name and r[3] == "total" for r in review)

    summary = {r[0]: r[1] for r in wb["Summary"].iter_rows(values_only=True) if r[0]}
    assert summary["Files processed"] == 3
    assert summary["Status NEEDS OCR (scanned, not read)"] == 1
    assert summary["Checks failed"] == 1

    lines = list(wb["LineItems"].iter_rows(min_row=2, values_only=True))
    assert len([r for r in lines if r[0] == paths[0].name]) == len(inv.items)


def test_cli_writes_workbook_and_json(batch, tmp_path, capsys):
    paths, _ = batch
    out, js = tmp_path / "o" / "result.xlsx", tmp_path / "result.json"
    assert main([str(paths[0].parent), "-o", str(out), "--json", str(js)]) == 0
    assert out.exists()
    data = json.loads(js.read_text(encoding="utf-8"))
    assert len(data) == 3 and data[0]["confidence"]["total"] > 0
    assert "3 files" in capsys.readouterr().out


def test_cli_json_dash_prints_results_to_stdout(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)  # so a stray file named "-" would show up here
    samples = Path(__file__).resolve().parent.parent / "samples" / "invoices"
    out = tmp_path / "result.xlsx"
    assert main([str(samples), "-o", str(out), "--json", "-"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)  # stdout is pure JSON, nothing else
    assert [d["file"] for d in data] == sorted(p.name for p in samples.glob("*.pdf"))
    by_file = {d["file"]: d for d in data}
    assert by_file["01_classic_us.pdf"]["status"] == "OK"
    assert by_file["08_total_does_not_add_up.pdf"]["status"] == "REVIEW"
    assert by_file["09_scanned_image_only.pdf"]["status"] == "NEEDS OCR"
    assert by_file["02_euro_comma_decimals.pdf"]["total"] == "117514.63"
    assert "9 files" in captured.err
    assert out.exists() and not (tmp_path / "-").exists()


def test_cli_errors_on_missing_or_empty_input(tmp_path):
    assert main([str(tmp_path / "nope")]) == 2
    assert main([str(tmp_path)]) == 2


def _reply(obj: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(obj)}}]}


def test_llm_fallback_is_not_called_when_nothing_is_low(invoice_pdf):
    path, _ = invoice_pdf("euro", 3)
    result = extract_invoice(path)

    def explode(_payload):
        raise AssertionError("model must not be called")

    assert apply_llm_fallback(result, path, "any-model", post=explode) == []


def test_llm_fallback_fills_blanks_but_keeps_them_in_review(text_pdf):
    path = text_pdf(
        [
            (50, 50, "Cedar Lane Catering"),
            (50, 90, "Invoice #: 5512"),
            (50, 120, "Raised 2 May 2026"),
            (50, 140, "To pay 108.00"),
        ]
    )
    result = extract_invoice(path)
    seen = {}

    def fake(payload):
        seen.update(payload)
        return _reply(
            {
                "invoice_date": "2026-05-02",
                "due_date": "2026-07-01",
                "total": "108.00",
                "subtotal": "99.99",
                "vendor": "Cedar Lane Catering",
            }
        )

    touched = apply_llm_fallback(result, path, "test-model", post=fake)
    assert seen["model"] == "test-model" and "Cedar Lane Catering" in seen["messages"][0]["content"]
    assert "invoice_number" not in seen["messages"][0]["content"].split("Invoice text:")[0]
    assert {"invoice_date", "total"} <= set(touched)
    assert result.get("total").value == Decimal("108.00")
    assert result.get("total").confidence < DEFAULT_THRESHOLD
    assert "not verified" in " ".join(result.get("total").reasons)
    assert "agrees" in " ".join(result.get("vendor").reasons)
    # values the model made up are not on the page, so they are refused
    for invented in ("due_date", "subtotal"):
        assert invented not in touched
        assert result.get(invented).value is None
        assert "not on the page: ignored" in " ".join(result.get(invented).reasons)


def test_llm_fallback_records_disagreement_and_survives_errors(text_pdf):
    path = text_pdf(
        [
            (50, 50, "Cedar Lane Catering"),
            (50, 90, "Invoice #: 5512"),
            (50, 120, "Deliver to: Someone Else Ltd"),
        ]
    )
    result = extract_invoice(path)
    apply_llm_fallback(result, path, "m", post=lambda _p: _reply({"vendor": "Someone Else Ltd"}))
    assert result.get("vendor").value == "Cedar Lane Catering"
    assert "suggests 'Someone Else Ltd'" in " ".join(result.get("vendor").reasons)

    def down(_payload):
        raise ConnectionError("no server")

    assert apply_llm_fallback(result, path, "m", post=down) == []
    assert "local model not available" in " ".join(result.get("total").reasons)


def test_llm_fallback_refuses_a_remote_server():
    from pdf_to_excel.llm import _post

    with pytest.raises(ValueError, match="local server"):
        _post({}, url="https://example.com/v1/chat/completions")


def test_no_em_dashes_in_the_repo():
    pattern = re.compile(chr(0x2014))
    checked = 0
    for path in ROOT.rglob("*"):
        skip = {".venv", ".git", "data", "__pycache__", ".pytest_cache", ".ruff_cache"}
        if (
            not path.is_file()
            or skip & set(path.parts)
            or path.suffix not in {".py", ".md", ".toml", ".yml", ".txt"}
        ):
            continue
        checked += 1
        assert not pattern.search(path.read_text(encoding="utf-8")), f"em dash in {path}"
    assert checked > 10
