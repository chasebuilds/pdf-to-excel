"""Result types shared by the extractor, the workbook writer and the benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

SCALAR_FIELDS = (
    "vendor",
    "invoice_number",
    "invoice_date",
    "due_date",
    "bill_to",
    "currency",
    "subtotal",
    "tax",
    "total",
)

DEFAULT_THRESHOLD = 0.80


@dataclass
class Field:
    value: Any = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    page: int | None = None
    method: str = ""
    raw: str = ""

    def lower(self, factor: float, reason: str) -> None:
        self.confidence = round(self.confidence * factor, 3)
        if reason not in self.reasons:
            self.reasons.append(reason)

    def raise_to(self, floor: float) -> None:
        self.confidence = round(max(self.confidence, floor), 3)


@dataclass
class LineItem:
    description: str = ""
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    page: int | None = None


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class InvoiceResult:
    file: str
    page_count: int = 0
    scanned_pages: list[int] = field(default_factory=list)
    needs_ocr: bool = False
    fields: dict[str, Field] = field(default_factory=dict)
    line_items: list[LineItem] = field(default_factory=list)
    line_items_reasons: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    error: str = ""
    # set when the file could not be read as an invoice at all:
    # ERROR, ENCRYPTED, EMPTY, NO TEXT, NEEDS OCR, BAD TEXT LAYER
    problem: str = ""
    problem_detail: str = ""
    file_notes: list[str] = field(default_factory=list)
    sha256: str = ""
    decimal_comma: bool | None = None

    def get(self, name: str) -> Field:
        return self.fields.get(name) or Field(reasons=["not found"])

    def min_confidence(self) -> float:
        return min((self.get(n).confidence for n in SCALAR_FIELDS), default=0.0)

    def as_dict(self) -> dict[str, Any]:
        def plain(v: Any) -> Any:
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, Decimal):
                return str(v)
            return v

        return {
            "file": self.file,
            "needs_ocr": self.needs_ocr,
            **{n: plain(self.get(n).value) for n in SCALAR_FIELDS},
            "confidence": {n: self.get(n).confidence for n in SCALAR_FIELDS},
            "line_items": [
                {
                    "description": li.description,
                    "quantity": plain(li.quantity),
                    "unit_price": plain(li.unit_price),
                    "amount": plain(li.amount),
                    "confidence": li.confidence,
                }
                for li in self.line_items
            ],
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
        }
