"""HTTP service: upload one invoice PDF, get JSON or a workbook back.

Install with ``pip install -e ".[api]"`` and run with
``uvicorn pdf_to_excel.api:app``. Every request is handled by the same
``extract_invoice`` and ``write_workbook`` the CLI uses.
"""

from __future__ import annotations

import re
import tempfile
from datetime import date
from pathlib import Path, PurePath
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import __version__
from .extract import extract_invoice
from .models import DEFAULT_THRESHOLD, SCALAR_FIELDS, InvoiceResult
from .workbook import review_rows, status_of, write_workbook

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

app = FastAPI(
    title="pdf-to-excel",
    version=__version__,
    description="Invoice PDF in, fields with a confidence score on each out.",
)

Threshold = Annotated[
    float,
    Query(ge=0.0, le=1.0, description="fields below this confidence are listed for review"),
]


class FieldOut(BaseModel):
    value: str | None
    confidence: float
    page: int | None = None
    reasons: list[str] = []


class LineItemOut(BaseModel):
    description: str
    quantity: str | None
    unit_price: str | None
    amount: str | None
    confidence: float
    page: int | None = None
    reasons: list[str] = []


class ReviewOut(BaseModel):
    sheet: str
    field: str
    value: str | None
    confidence: float | None
    reason: str
    page: str | None


class CheckOut(BaseModel):
    name: str
    passed: bool
    detail: str


class ExtractResponse(BaseModel):
    file: str
    status: str
    sha256: str
    page_count: int
    min_confidence: float
    fields: dict[str, FieldOut]
    line_items: list[LineItemOut]
    review: list[ReviewOut]
    checks: list[CheckOut]


class Health(BaseModel):
    status: str
    version: str


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def to_response(r: InvoiceResult, threshold: float) -> ExtractResponse:
    fields = {
        n: FieldOut(
            value=_text(r.get(n).value),
            confidence=r.get(n).confidence,
            page=r.get(n).page,
            reasons=list(r.get(n).reasons),
        )
        for n in SCALAR_FIELDS
    }
    items = [
        LineItemOut(
            description=li.description,
            quantity=_text(li.quantity),
            unit_price=_text(li.unit_price),
            amount=_text(li.amount),
            confidence=li.confidence,
            page=li.page,
            reasons=list(li.reasons),
        )
        for li in r.line_items
    ]
    # review_rows is the Review sheet's source: file, invoice, sheet, field, value, conf, why, page
    review = [
        ReviewOut(
            sheet=row[2],
            field=row[3],
            value=_text(row[4]),
            confidence=row[5] if isinstance(row[5], float) else None,
            reason=row[6],
            page=_text(row[7]),
        )
        for row in review_rows(r, threshold)
    ]
    return ExtractResponse(
        file=r.file,
        status=status_of(r, threshold),
        sha256=r.sha256,
        page_count=r.page_count,
        min_confidence=r.min_confidence(),
        fields=fields,
        line_items=items,
        review=review,
        checks=[CheckOut(name=c.name, passed=c.passed, detail=c.detail) for c in r.checks],
    )


async def _read_upload(upload: UploadFile, workdir: Path) -> Path:
    """Save the upload under a safe name, after checking it is a PDF of sane size."""
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file is over {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    if data.lstrip()[:5] != b"%PDF-":
        raise HTTPException(422, "not a PDF: the upload does not start with a PDF header")
    name = PurePath(upload.filename or "upload.pdf").name or "upload.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    path = workdir / name
    path.write_bytes(data)
    return path


def _extract(path: Path) -> InvoiceResult:
    result = extract_invoice(path)
    if result.problem:
        # NEEDS OCR, BAD TEXT LAYER, ENCRYPTED, ERROR, ...: nothing usable was read
        raise HTTPException(
            422, {"status": result.problem, "reason": result.problem_detail, "file": result.file}
        )
    return result


@app.get("/health")
def health() -> Health:
    return Health(status="ok", version=__version__)


@app.post("/extract")
async def extract(file: UploadFile, threshold: Threshold = DEFAULT_THRESHOLD) -> ExtractResponse:
    with tempfile.TemporaryDirectory() as tmp:
        result = _extract(await _read_upload(file, Path(tmp)))
    return to_response(result, threshold)


@app.post("/extract.xlsx", response_class=Response)
async def extract_xlsx(file: UploadFile, threshold: Threshold = DEFAULT_THRESHOLD) -> Response:
    with tempfile.TemporaryDirectory() as tmp:
        result = _extract(await _read_upload(file, Path(tmp)))
        out = write_workbook([result], Path(tmp) / "invoices.xlsx", threshold)
        body = out.read_bytes()
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", PurePath(result.file).stem)
    return Response(
        body,
        media_type=XLSX_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'},
    )
