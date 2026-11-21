"""Seeded generator: synthetic invoices in ten layouts, with ground truth.

Every PDF gets a JSON file of the values that were printed on it. All
names, addresses and numbers are made up from the word lists below.

    python -m benchmark.generate --out benchmark/data --per-layout 40
"""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

# Round 1 held out letter, services and swiss. That run found two bugs, so those
# three became tuning layouts and two new layouts were written for round 2.
ROUND1_HELD_OUT = ("letter", "services", "swiss")
TUNING_LAYOUTS = ("classic", "euro", "minimal", "ledger", "boxed", *ROUND1_HELD_OUT)
HELD_OUT_LAYOUTS = ("retail", "statement")
# Built to be hard, never tuned on, scored separately. The point is not accuracy
# but whether the mistakes they cause are sent to review.
STRESS_LAYOUTS = ("stress_wording", "stress_ambiguous", "stress_table")
LAYOUTS = TUNING_LAYOUTS + HELD_OUT_LAYOUTS

NAME_A = [
    "Alder", "Brightwater", "Copperfield", "Dunmore", "Eastgate", "Fernhill", "Granite Peak",
    "Harborview", "Ironwood", "Juniper", "Kestrel", "Lakeshore", "Maplestone", "Northgate",
    "Oakline", "Pinecrest", "Quarry Lane", "Redfern", "Silverbrook", "Tidewater", "Upland",
    "Vantage", "Westmere", "Yarrow",
]  # fmt: skip
NAME_B = [
    "Supply Co.", "Logistics LLC", "Design Studio", "Consulting Group", "Hardware Ltd",
    "Print Works", "Systems Inc.", "Trading GmbH", "Foods", "Electric", "& Sons", "Partners",
    "Media", "Landscaping", "Dental Lab", "Freight", "Office Solutions", "Bakery", "Roofing",
    "Analytics Ltd",
]  # fmt: skip
FIRST = ["Dana", "Morgan", "Riley", "Jordan", "Casey", "Avery", "Quinn", "Reese", "Rowan", "Tatum"]
LAST = [
    "Whitfield",
    "Okafor",
    "Lindqvist",
    "Marchetti",
    "Dubois",
    "Tanaka",
    "Novak",
    "Hale",
    "Ibarra",
]
STREETS = [
    "Mill Road",
    "Canal Street",
    "Orchard Way",
    "Foundry Lane",
    "Station Avenue",
    "Birch Court",
]
CITIES = ["Riverton", "Lakefield", "Ashbury", "Northam", "Greywell", "Stonebridge", "Elmhurst"]
ITEMS = [
    "Copy paper, A4, 80 gsm, box of 5 reams", "Toner cartridge, black", "Standing desk frame",
    "Ergonomic office chair", "USB-C docking station", "Monitor arm, dual", "Network switch, 24 port",
    "Cat6 patch cable, 3 m", "Label printer ribbon", "Whiteboard markers, pack of 12",
    "Shipping carton, large", "Packing tape, 48 mm", "Pallet wrap, 500 m roll",
    "Website maintenance", "Logo design revision", "Database tuning", "On-site installation",
    "Tax filing support", "Total care service plan", "Delivery van rental, per day",
    "Discount card printing", "Safety gloves, nitrile, box", "LED panel light 600x600",
    "Floor cleaning concentrate, 5 L", "Coffee beans, 1 kg", "Stainless bolts M8, bag of 100",
    "Plywood sheet 18 mm", "Paint, matte white, 10 L", "Hydraulic hose assembly",
    "Annual software licence", "Cloud backup storage, 1 TB", "Training workshop, half day",
    "Photography session", "Translation, per 1000 words", "Bookkeeping, monthly",
]  # fmt: skip
LONG_ITEMS = [
    "Discovery workshop with the operations team, including preparation and written summary",
    "Data migration from the legacy system: mapping, trial load, reconciliation and sign-off",
    "Monthly reporting pack: sales by region, margin by product line, and stock ageing",
    "Design and build of the customer intake form, with validation rules and email routing",
    "Spreadsheet clean-up: duplicate removal, date repair, and standardised supplier names",
    "Process documentation for month-end close, reviewed with the finance lead",
    "Support retainer covering bug fixes and small changes requested during the period",
    "Site survey and measured drawings for the ground floor, delivered as PDF and DWG",
]


@dataclass
class Item:
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    discount: Decimal = Decimal(0)  # percent, stress_table only


@dataclass
class Invoice:
    layout: str
    vendor: str
    vendor_addr: list[str]
    vendor_email: str
    number: str
    invoice_date: date
    due_date: date
    bill_to: str
    bill_addr: list[str]
    currency: str
    items: list[Item]
    tax_rate: Decimal
    shipping: Decimal | None = None
    po: str | None = None
    customer_no: str | None = None
    order_date: date | None = None
    total_override: Decimal | None = None  # tests use this to print a wrong total
    deposit: Decimal | None = None  # stress_ambiguous prints a balance after it
    subtotal: Decimal = field(init=False)
    tax: Decimal = field(init=False)
    total: Decimal = field(init=False)

    def __post_init__(self) -> None:
        self.subtotal = sum((i.amount for i in self.items), Decimal(0))
        self.tax = q2(self.subtotal * self.tax_rate / 100)
        self.total = self.subtotal + self.tax + (self.shipping or Decimal(0))

    def truth(self) -> dict:
        return {
            "layout": self.layout,
            "scanned": False,
            "vendor": self.vendor,
            "invoice_number": self.number,
            "invoice_date": self.invoice_date.isoformat(),
            "due_date": self.due_date.isoformat(),
            "bill_to": self.bill_to,
            "currency": self.currency,
            "subtotal": str(self.subtotal),
            "tax": str(self.tax),
            "total": str(self.total),
            "line_items": [
                {
                    "description": i.description,
                    "quantity": str(i.quantity),
                    "unit_price": str(i.unit_price),
                    "amount": str(i.amount),
                }
                for i in self.items
            ],
        }


def q2(d: Decimal) -> Decimal:
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---- formatting ----------------------------------------------------------


def fmt_num(d: Decimal, style: str) -> str:
    s = f"{d:,.2f}"
    if style == "us":
        return s
    if style == "plain":
        return s.replace(",", "")
    thousands, dec = {"eu": (".", ","), "space": (" ", ","), "swiss": ("'", ".")}[style]
    return s.replace(",", "\0").replace(".", dec).replace("\0", thousands)


def fmt_qty(q: Decimal, style: str, fixed: bool = False) -> str:
    s = f"{q:.2f}" if fixed else (str(int(q)) if q == q.to_integral() else str(q.normalize()))
    return s.replace(".", ",") if style in ("eu", "space") else s


# ---- drawing helpers -----------------------------------------------------


def put(c: Canvas, x: float, y: float, s: str, font: str = "Helvetica", size: float = 10,
        align: str = "l", color=colors.black) -> None:  # fmt: skip
    c.setFont(font, size)
    c.setFillColor(color)
    {"l": c.drawString, "r": c.drawRightString, "c": c.drawCentredString}[align](x, y, s)


def wrap(s: str, font: str, size: float, width: float) -> list[str]:
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if stringWidth(trial, font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return [*lines, cur]


@dataclass
class Col:
    key: str  # description, quantity, unit_price, amount, index, code
    header: str
    width: float
    align: str = "l"
    header_align: str | None = None


@dataclass
class TableStyle:
    font: str = "Helvetica"
    bold: str = "Helvetica-Bold"
    size: float = 9.5
    row_h: float = 17
    grid: bool = False
    rules: bool = False
    zebra: bool = False
    header_fill: colors.Color | None = None
    header_color: colors.Color = colors.black
    header_upper: bool = False
    repeat_header: bool = True
    num_style: str = "us"
    qty_fixed: bool = False
    cell_money: str = "{n}"
    bottom: float = 80


def draw_table(c: Canvas, inv: Invoice, cols: list[Col], x: float, y: float, st: TableStyle,
               new_page: Callable[[], float]) -> float:  # fmt: skip
    total_w = sum(col.width for col in cols)
    pad = 5

    def header(y: float) -> float:
        if st.header_fill:
            c.setFillColor(st.header_fill)
            c.rect(x, y - 5, total_w, st.row_h, stroke=0, fill=1)
        cx = x
        for col in cols:
            al = col.header_align or col.align
            tx = {"l": cx + pad, "r": cx + col.width - pad, "c": cx + col.width / 2}[al]
            put(c, tx, y, col.header.upper() if st.header_upper else col.header, st.bold,
                st.size - (1.5 if st.header_upper else 0), al, st.header_color)  # fmt: skip
            cx += col.width
        c.setStrokeColor(colors.black)
        if st.grid:
            c.setLineWidth(0.5)
            c.rect(x, y - 5, total_w, st.row_h, stroke=1, fill=0)
        if st.rules:
            c.setLineWidth(0.8)
            c.line(x, y - 5, x + total_w, y - 5)
            c.line(x, y + st.row_h - 5, x + total_w, y + st.row_h - 5)
        return y - st.row_h

    y = header(y)
    desc_col = next(col for col in cols if col.key == "description")
    for n, item in enumerate(inv.items, start=1):
        lines = wrap(item.description, st.font, st.size, desc_col.width - 2 * pad)
        h = st.row_h + (len(lines) - 1) * (st.size + 2)
        if y - h < st.bottom:
            c.showPage()
            y = new_page()
            if st.repeat_header:
                y = header(y)
        if st.zebra and n % 2 == 0:
            c.setFillColor(colors.Color(0.95, 0.95, 0.95))
            c.rect(x, y - h + st.row_h - 5, total_w, h, stroke=0, fill=1)
        values = {
            "index": str(n),
            "vat": f"{inv.tax_rate}%",
            "disc": f"{item.discount}%" if item.discount else "",
            "code": f"{(n * 37 + len(item.description)) % 900 + 100}-{n:02d}",
            "quantity": fmt_qty(item.quantity, st.num_style, st.qty_fixed),
            "unit_price": st.cell_money.format(n=fmt_num(item.unit_price, st.num_style)),
            "amount": st.cell_money.format(n=fmt_num(item.amount, st.num_style)),
        }
        cx = x
        for col in cols:
            tx = {"l": cx + pad, "r": cx + col.width - pad, "c": cx + col.width / 2}[col.align]
            if col.key == "description":
                for k, text in enumerate(lines):
                    put(c, tx, y - k * (st.size + 2), text, st.font, st.size, col.align)
            else:
                put(c, tx, y, values[col.key], st.font, st.size, col.align)
            cx += col.width
        if st.grid:
            c.setLineWidth(0.5)
            c.setStrokeColor(colors.black)
            c.rect(x, y - h + st.row_h - 5, total_w, h, stroke=1, fill=0)
        elif st.rules:
            c.setLineWidth(0.3)
            c.setStrokeColor(colors.grey)
            c.line(x, y - h + st.row_h - 5, x + total_w, y - h + st.row_h - 5)
        y -= h
    return y


def draw_totals(c: Canvas, rows: list[tuple[str, str]], x_label: float, x_value: float, y: float,
                font: str = "Helvetica", bold: str = "Helvetica-Bold", size: float = 10,
                label_align: str = "l", big_last: float = 0, gap: float = 17) -> float:  # fmt: skip
    for i, (label, value) in enumerate(rows):
        last = i == len(rows) - 1
        f, s = (bold, size + big_last) if last else (font, size)
        if last and big_last:
            y -= big_last
        put(c, x_label, y, label, f, s, label_align)
        put(c, x_value, y, value, f, s, "r")
        y -= gap
    return y


def room_for_totals(c: Canvas, y: float, new_page: Callable[[], float], need: float = 130) -> float:
    if y < need:
        c.showPage()
        return new_page()
    return y


def printed_total(inv: Invoice) -> Decimal:
    return inv.total_override if inv.total_override is not None else inv.total


# ---- layouts -------------------------------------------------------------


def layout_classic(c: Canvas, inv: Invoice) -> None:
    """US letter, seller top left, labelled block top right, grid table, $ amounts."""
    w, h = LETTER
    m = lambda d: "$" + fmt_num(d, "us")  # noqa: E731
    put(c, 50, h - 60, inv.vendor, "Helvetica-Bold", 18)
    for i, line in enumerate([*inv.vendor_addr, inv.vendor_email]):
        put(c, 50, h - 78 - 12 * i, line, size=9)
    put(c, w - 50, h - 62, "INVOICE", "Helvetica-Bold", 22, "r", colors.Color(0.3, 0.3, 0.3))
    meta = [("Invoice #:", inv.number), ("Invoice Date:", inv.invoice_date.strftime("%m/%d/%Y")),
            ("Due Date:", inv.due_date.strftime("%m/%d/%Y"))]  # fmt: skip
    if inv.po:
        meta.append(("PO Number:", inv.po))
    for i, (label, value) in enumerate(meta):
        put(c, w - 150, h - 90 - 15 * i, label, "Helvetica-Bold", 10, "r")
        put(c, w - 142, h - 90 - 15 * i, value)
    put(c, 50, h - 170, "Bill To:", "Helvetica-Bold", 10)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 50, h - 185 - 13 * i, line)
    cols = [Col("description", "Description", 272), Col("quantity", "Qty", 60, "r"),
            Col("unit_price", "Unit Price", 90, "r"), Col("amount", "Amount", 90, "r")]  # fmt: skip
    st = TableStyle(grid=True, header_fill=colors.Color(0.88, 0.88, 0.88), cell_money="${n}")
    y = draw_table(c, inv, cols, 50, h - 260, st, lambda: h - 70)
    y = room_for_totals(c, y - 8, lambda: h - 70)
    rows = [("Subtotal", m(inv.subtotal)), (f"Tax ({inv.tax_rate}%)", m(inv.tax))]
    if inv.shipping:
        rows.append(("Shipping", m(inv.shipping)))
    rows.append(("Total", m(printed_total(inv))))
    draw_totals(c, rows, w - 220, w - 55, y)
    put(c, 50, 60, "Payment terms: Net 30. Thank you for your business.", size=8.5)


def layout_euro(c: Canvas, inv: Invoice) -> None:
    """A4, centred seller, dotted dates, comma decimals, quantity first."""
    w, h = A4
    n = lambda d: fmt_num(d, "eu")  # noqa: E731
    put(c, w / 2, h - 55, inv.vendor, "Helvetica-Bold", 16, "c")
    put(c, w / 2, h - 70, " · ".join(inv.vendor_addr), size=8.5, align="c")
    c.setLineWidth(1)
    c.line(50, h - 80, w - 50, h - 80)
    put(c, 50, h - 115, "Customer:", "Helvetica-Bold", 10)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 50, h - 130 - 13 * i, line)
    meta = [("Invoice No.", inv.number), ("Date", inv.invoice_date.strftime("%d.%m.%Y")),
            ("Payment due", inv.due_date.strftime("%d.%m.%Y"))]  # fmt: skip
    if inv.customer_no:
        meta.append(("Customer No.", inv.customer_no))
    for i, (label, value) in enumerate(meta):
        put(c, 350, h - 115 - 15 * i, label)
        put(c, 440, h - 115 - 15 * i, value, "Helvetica-Bold")
    put(c, 50, h - 215, "INVOICE", "Helvetica-Bold", 13)
    cols = [Col("quantity", "Qty", 45, "c"), Col("description", "Description", 270),
            Col("unit_price", "Unit price", 90, "r"), Col("amount", "Line total", 90, "r")]  # fmt: skip
    st = TableStyle(rules=True, num_style="eu")
    y = draw_table(c, inv, cols, 50, h - 250, st, lambda: h - 70)
    y = room_for_totals(c, y - 10, lambda: h - 70)
    rows = [("Net amount", n(inv.subtotal) + " €"), (f"VAT {inv.tax_rate} %", n(inv.tax) + " €"),
            ("Amount due", n(printed_total(inv)) + " €")]  # fmt: skip
    draw_totals(c, rows, 330, w - 55, y)
    put(c, 50, 55, "VAT ID: DE 000 111 222 · Bank: Example Bank · Payment within 30 days", size=8)


def layout_minimal(c: Canvas, inv: Invoice) -> None:
    """Labels stacked above values in one row, no grid, GBP, long dates."""
    w, h = LETTER
    m = lambda d: "£" + fmt_num(d, "us")  # noqa: E731
    grey = colors.Color(0.45, 0.45, 0.45)
    put(c, 50, h - 75, "INVOICE", "Helvetica", 30, color=grey)
    put(c, w - 50, h - 60, inv.vendor, "Helvetica-Bold", 12, "r")
    put(c, w - 50, h - 74, inv.vendor_email, size=8.5, align="r", color=grey)
    stacked = [(50, "INVOICE NUMBER", inv.number),
               (190, "DATE OF ISSUE", inv.invoice_date.strftime("%d %B %Y").lstrip("0")),
               (330, "DUE DATE", inv.due_date.strftime("%d %B %Y").lstrip("0")),
               (460, "BILLED TO", inv.bill_to)]  # fmt: skip
    for x, label, value in stacked:
        put(c, x, h - 130, label, size=7, color=grey)
        put(c, x, h - 144, value, size=10)
    for i, line in enumerate(inv.bill_addr):
        put(c, 460, h - 157 - 12 * i, line, size=8.5, color=grey)
    cols = [Col("description", "Item", 262), Col("unit_price", "Rate", 90, "r"),
            Col("quantity", "Qty", 60, "r"), Col("amount", "Total", 100, "r")]  # fmt: skip
    st = TableStyle(zebra=True, header_upper=True, header_color=grey, cell_money="£{n}", row_h=19)
    y = draw_table(c, inv, cols, 50, h - 225, st, lambda: h - 70)
    y = room_for_totals(c, y - 12, lambda: h - 70)
    rows = [
        ("Sub-total", m(inv.subtotal)),
        ("Sales Tax", m(inv.tax)),
        ("Balance Due", m(printed_total(inv))),
    ]
    draw_totals(c, rows, w - 230, w - 55, y, big_last=4)


def layout_ledger(c: Canvas, inv: Invoice) -> None:
    """Many lines over several pages, serif font, ISO dates, currency code after the amount."""
    w, h = LETTER
    n = lambda d: fmt_num(d, "us")  # noqa: E731
    page = [1]

    def top() -> float:
        put(c, 50, h - 50, inv.vendor, "Times-Bold", 14)
        put(c, w - 50, h - 50, "Invoice", "Times-Italic", 14, "r")
        put(c, w / 2, 40, f"Page {page[0]}", "Times-Roman", 9, "c")
        return h - 75

    def next_page() -> float:
        page[0] += 1
        y = top()
        put(c, 50, y, f"Reference: {inv.number} (continued)", "Times-Roman", 9)
        return y - 25

    y = top()
    put(c, 50, y, ", ".join(inv.vendor_addr), "Times-Roman", 9)
    y -= 26
    put(c, 50, y, f"Reference: {inv.number}", "Times-Roman", 10.5)
    put(c, 230, y, f"Issued: {inv.invoice_date.isoformat()}", "Times-Roman", 10.5)
    put(c, 400, y, f"Due: {inv.due_date.isoformat()}", "Times-Roman", 10.5)
    y -= 22
    put(c, 50, y, f"Sold To: {inv.bill_to}", "Times-Roman", 10.5)
    put(c, 50, y - 13, ", ".join(inv.bill_addr), "Times-Roman", 9)
    if inv.po:
        put(c, 400, y, f"Order No: {inv.po}", "Times-Roman", 10.5)
    cols = [Col("index", "#", 30, "r"), Col("description", "Description", 262),
            Col("quantity", "Quantity", 65, "r"), Col("unit_price", "Price", 75, "r"),
            Col("amount", "Amount", 80, "r")]  # fmt: skip
    st = TableStyle(
        font="Times-Roman", bold="Times-Bold", size=10, row_h=15, rules=True, qty_fixed=True
    )
    y = draw_table(c, inv, cols, 50, y - 50, st, next_page)
    y = room_for_totals(c, y - 8, next_page)
    rows = [("Subtotal:", n(inv.subtotal) + " USD"), ("Tax:", n(inv.tax) + " USD"),
            ("Total:", n(printed_total(inv)) + " USD")]  # fmt: skip
    draw_totals(c, rows, w - 240, w - 55, y, "Times-Roman", "Times-Bold")


def layout_boxed(c: Canvas, inv: Invoice) -> None:
    """Colour band, boxed label/value grid, amount column before quantity, CAD prefix."""
    w, h = LETTER
    n = lambda d: fmt_num(d, "us")  # noqa: E731
    navy = colors.Color(0.1, 0.2, 0.4)
    c.setFillColor(navy)
    c.rect(0, h - 80, w, 80, stroke=0, fill=1)
    put(c, 50, h - 50, inv.vendor, "Helvetica-Bold", 16, color=colors.white)
    put(c, w - 50, h - 50, "TAX INVOICE", "Helvetica", 13, "r", colors.white)
    meta = [("Inv No", inv.number), ("Date", inv.invoice_date.strftime("%b %d, %Y")),
            ("Due", inv.due_date.strftime("%b %d, %Y")), ("Terms", "Net 30")]  # fmt: skip
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    for i, (label, value) in enumerate(meta):
        y = h - 115 - 18 * i
        c.rect(360, y - 5, 70, 18, stroke=1, fill=0)
        c.rect(430, y - 5, 132, 18, stroke=1, fill=0)
        put(c, 365, y, label, "Helvetica-Bold", 9)
        put(c, 435, y, value, size=9)
    put(c, 50, h - 110, "Client", size=8, color=colors.grey)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 50, h - 124 - 13 * i, line, "Helvetica-Bold" if i == 0 else "Helvetica")
    put(c, 50, h - 180, " | ".join(inv.vendor_addr), size=8, color=colors.grey)
    cols = [Col("description", "Description", 262), Col("amount", "Amount", 90, "r"),
            Col("quantity", "Qty", 60, "r"), Col("unit_price", "Unit Cost", 100, "r")]  # fmt: skip
    st = TableStyle(grid=True, header_fill=navy, header_color=colors.white)
    y = draw_table(c, inv, cols, 50, h - 225, st, lambda: h - 70)
    y = room_for_totals(c, y - 10, lambda: h - 70)
    rows = [("Subtotal", "CAD " + n(inv.subtotal)), (f"GST ({inv.tax_rate}%)", "CAD " + n(inv.tax)),
            ("Total Due", "CAD " + n(printed_total(inv)))]  # fmt: skip
    c.rect(w - 262, y - 17 * len(rows) + 8, 212, 17 * len(rows) + 6, stroke=1, fill=0)
    draw_totals(c, rows, w - 255, w - 57, y)


def layout_letter(c: Canvas, inv: Invoice) -> None:
    """Held out. Window-envelope letter, DD/MM/YYYY, space thousands, code column."""
    w, h = A4
    n = lambda d: fmt_num(d, "space")  # noqa: E731
    put(c, 50, h - 55, inv.vendor, "Helvetica-Bold", 13)
    for i, line in enumerate(inv.vendor_addr):
        put(c, 50, h - 69 - 11 * i, line, size=8.5)
    put(c, 340, h - 130, "Invoice To", size=8, color=colors.grey)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 340, h - 144 - 13 * i, line)
    put(c, 50, h - 230, "Invoice", "Helvetica-Bold", 16)
    meta = [f"Document No: {inv.number}", f"Document Date: {inv.invoice_date.strftime('%d/%m/%Y')}",
            f"Pay By: {inv.due_date.strftime('%d/%m/%Y')}"]  # fmt: skip
    if inv.customer_no:
        meta.append(f"Account No: {inv.customer_no}")
    for i, line in enumerate(meta):
        put(c, 50, h - 250 - 14 * i, line)
    cols = [Col("code", "Code", 55), Col("description", "Details", 230), Col("quantity", "Units", 50, "r"),
            Col("unit_price", "Unit Price", 80, "r"), Col("amount", "Net", 80, "r")]  # fmt: skip
    st = TableStyle(rules=True, num_style="space", size=9)
    y = draw_table(c, inv, cols, 50, h - 335, st, lambda: h - 70)
    y = room_for_totals(c, y - 10, lambda: h - 70)
    rows = [("Total excl. tax", n(inv.subtotal) + " EUR"), (f"Tax {inv.tax_rate} %", n(inv.tax) + " EUR"),
            ("Total incl. tax", n(printed_total(inv)) + " EUR")]  # fmt: skip
    draw_totals(c, rows, 330, w - 55, y)


def layout_services(c: Canvas, inv: Invoice) -> None:
    """Held out. Centred coloured seller name, wrapped descriptions, hours and rate, no thousands separator."""
    w, h = LETTER
    m = lambda d: "$" + fmt_num(d, "plain")  # noqa: E731
    teal = colors.Color(0.0, 0.45, 0.45)
    put(c, w / 2, h - 60, inv.vendor, "Helvetica-Bold", 20, "c", teal)
    put(c, w / 2, h - 76, "INVOICE", size=9, align="c", color=colors.grey)
    put(c, 50, h - 120, "Bill To", "Helvetica-Bold", 9, color=teal)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 50, h - 134 - 13 * i, line)
    meta = [("Invoice ID", inv.number), ("Date of Issue", inv.invoice_date.strftime("%d-%b-%Y")),
            ("Payment Due", inv.due_date.strftime("%d-%b-%Y"))]  # fmt: skip
    for i, (label, value) in enumerate(meta):
        put(c, 380, h - 120 - 15 * i, label, "Helvetica-Bold", 9)
        put(c, w - 50, h - 120 - 15 * i, value, align="r")
    cols = [Col("description", "Description", 290), Col("quantity", "Hours", 60, "r"),
            Col("unit_price", "Rate", 75, "r"), Col("amount", "Amount", 87, "r")]  # fmt: skip
    st = TableStyle(rules=True, num_style="plain", cell_money="${n}", header_color=teal)
    y = draw_table(c, inv, cols, 50, h - 215, st, lambda: h - 70)
    y = room_for_totals(c, y - 10, lambda: h - 70)
    rows = [("Subtotal", m(inv.subtotal)), (f"Tax {inv.tax_rate}%", m(inv.tax))]
    if inv.shipping:
        rows.append(("Delivery", m(inv.shipping)))
    rows.append(("Amount Due", m(printed_total(inv))))
    draw_totals(c, rows, w - 220, w - 55, y)
    put(
        c,
        w / 2,
        55,
        " | ".join([*inv.vendor_addr, inv.vendor_email]),
        size=8,
        align="c",
        color=colors.grey,
    )


def layout_swiss(c: Canvas, inv: Invoice) -> None:
    """Held out. Seller top right, labels without colons, apostrophe thousands, price before quantity,
    header not repeated on later pages."""
    w, h = A4
    n = lambda d: "CHF " + fmt_num(d, "swiss")  # noqa: E731
    put(c, w - 50, h - 55, inv.vendor, "Helvetica-Bold", 14, "r")
    for i, line in enumerate(inv.vendor_addr):
        put(c, w - 50, h - 69 - 11 * i, line, size=8.5, align="r")
    put(c, 50, h - 60, "SALES INVOICE", "Helvetica-Bold", 12)
    meta = [("Invoice Number", inv.number), ("Dated", inv.invoice_date.strftime("%d %b %Y")),
            ("Due on", inv.due_date.strftime("%d %b %Y"))]  # fmt: skip
    if inv.order_date:
        meta.insert(1, ("Order Date", inv.order_date.strftime("%d %b %Y")))
    meta.append(("Customer", inv.bill_to))
    for i, (label, value) in enumerate(meta):
        put(c, 50, h - 100 - 15 * i, label, size=9.5, color=colors.Color(0.35, 0.35, 0.35))
        put(c, 150, h - 100 - 15 * i, value, size=9.5)
    put(c, 150, h - 100 - 15 * len(meta), ", ".join(inv.bill_addr), size=8.5)
    cols = [Col("index", "Pos", 35), Col("description", "Product", 240), Col("unit_price", "Price", 80, "r"),
            Col("quantity", "Qty", 50, "r"), Col("amount", "Total", 90, "r")]  # fmt: skip
    st = TableStyle(zebra=True, num_style="swiss", size=9, row_h=16, repeat_header=False,
                    header_fill=colors.Color(0.9, 0.9, 0.9))  # fmt: skip
    y = draw_table(c, inv, cols, 50, h - 215, st, lambda: h - 60)
    y = room_for_totals(c, y - 10, lambda: h - 60)
    rows = [("Net Total", n(inv.subtotal)), (f"VAT {inv.tax_rate}%", n(inv.tax)),
            ("Grand Total", n(printed_total(inv)))]  # fmt: skip
    draw_totals(c, rows, 320, w - 55, y)


def layout_retail(c: Canvas, inv: Invoice) -> None:
    """Held out, round 2. Seller in a logo box, boxed strip of stacked labels, sold-to beside
    ship-to, SKU column, dashed dates."""
    w, h = LETTER
    m = lambda d: "$" + fmt_num(d, "us")  # noqa: E731
    c.setLineWidth(1)
    c.rect(50, h - 95, 190, 50, stroke=1, fill=0)
    put(c, 145, h - 75, inv.vendor, "Helvetica-Bold", 12, "c")
    put(c, 50, h - 107, ", ".join(inv.vendor_addr), size=8)
    put(c, w - 50, h - 70, "Invoice", "Helvetica", 20, "r")
    strip = [("Invoice No", inv.number), ("Invoice Date", inv.invoice_date.strftime("%m-%d-%Y")),
             ("Terms", "Net 30"), ("Date Due", inv.due_date.strftime("%m-%d-%Y"))]  # fmt: skip
    c.setLineWidth(0.5)
    for i, (label, value) in enumerate(strip):
        x = 50 + 128 * i
        c.rect(x, h - 165, 128, 34, stroke=1, fill=0)
        put(c, x + 6, h - 142, label, size=7.5, color=colors.grey)
        put(c, x + 6, h - 157, value, size=10)
    put(c, 50, h - 195, "Sold To", "Helvetica-Bold", 9)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 50, h - 209 - 13 * i, line)
    put(c, 320, h - 195, "Ship To", "Helvetica-Bold", 9)
    for i, line in enumerate(["Receiving Dock 2", *inv.bill_addr]):
        put(c, 320, h - 209 - 13 * i, line)
    cols = [Col("code", "SKU", 60), Col("description", "Item", 222), Col("quantity", "Qty", 50, "r"),
            Col("unit_price", "Each", 80, "r"), Col("amount", "Extended", 100, "r")]  # fmt: skip
    st = TableStyle(grid=True, size=9)
    y = draw_table(c, inv, cols, 50, h - 285, st, lambda: h - 70)
    y = room_for_totals(c, y - 10, lambda: h - 70)
    rows = [("Subtotal", m(inv.subtotal)), (f"Sales Tax {inv.tax_rate}%", m(inv.tax))]
    if inv.shipping:
        rows.append(("Shipping", m(inv.shipping)))
    rows.append(("Total Due", m(printed_total(inv))))
    draw_totals(c, rows, w - 230, w - 55, y)
    put(c, 50, 55, f"Make all checks payable to {inv.vendor}.", size=8.5)


def layout_statement(c: Canvas, inv: Invoice) -> None:
    """Held out, round 2. Centred title, FROM and BILL TO side by side, long month-first dates,
    euro sign before the amount, a VAT rate column in the table."""
    w, h = A4
    m = lambda d: "€ " + fmt_num(d, "eu")  # noqa: E731
    grey = colors.Color(0.4, 0.4, 0.4)
    put(c, w / 2, h - 60, "INVOICE", "Helvetica-Bold", 22, "c")
    put(c, 50, h - 110, "FROM", size=8, color=grey)
    put(c, 50, h - 124, inv.vendor, "Helvetica-Bold", 11)
    for i, line in enumerate(inv.vendor_addr):
        put(c, 50, h - 137 - 12 * i, line, size=9)
    put(c, 220, h - 110, "BILL TO", size=8, color=grey)
    put(c, 220, h - 124, inv.bill_to, "Helvetica-Bold", 11)
    for i, line in enumerate(inv.bill_addr):
        put(c, 220, h - 137 - 12 * i, line, size=9)
    day = lambda d: f"{d.strftime('%B')} {d.day}, {d.year}"  # noqa: E731
    meta = [
        ("Invoice No.", inv.number),
        ("Issue Date", day(inv.invoice_date)),
        ("Due Date", day(inv.due_date)),
    ]
    if inv.po:
        meta.append(("PO", inv.po))
    for i, (label, value) in enumerate(meta):
        put(c, 400, h - 110 - 14 * i, label, size=9, color=grey)
        put(c, 460, h - 110 - 14 * i, value, size=9)
    cols = [Col("description", "Description", 200), Col("unit_price", "Unit Price", 85, "r"),
            Col("quantity", "Quantity", 60, "r"), Col("vat", "VAT", 50, "r"), Col("amount", "Total", 100, "r")]  # fmt: skip
    st = TableStyle(rules=True, num_style="eu", cell_money="€ {n}", size=9)
    y = draw_table(c, inv, cols, 50, h - 230, st, lambda: h - 70)
    y = room_for_totals(c, y - 10, lambda: h - 70)
    rows = [("Subtotal", m(inv.subtotal)), ("VAT", m(inv.tax)), ("Total", m(printed_total(inv)))]
    draw_totals(c, rows, 340, w - 55, y)
    put(c, 50, 55, "Bank transfer: IBAN XX00 0000 0000 0000 · BIC EXAMPLEXX", size=8, color=grey)


def layout_stress_wording(c: Canvas, inv: Invoice) -> None:
    """Stress. No familiar wording anywhere: Our Ref, Raised, Settle by, Particulars/Nos/@/Value,
    Goods, To pay. Customer block has no label. Seller appears only in the footer."""
    w, h = A4
    m = lambda d: "£" + fmt_num(d, "us")  # noqa: E731
    long = lambda d: f"{d.day} {d.strftime('%B %Y')}"  # noqa: E731
    put(c, 50, h - 60, "INVOICE", "Helvetica-Bold", 18)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 50, h - 110 - 13 * i, line)
    meta = [
        f"Our Ref: {inv.number}",
        f"Raised: {long(inv.invoice_date)}",
        f"Settle by: {long(inv.due_date)}",
    ]
    if inv.po:
        meta.insert(1, f"Your Ref: {inv.po}")
    for i, line in enumerate(meta):
        put(c, 360, h - 110 - 14 * i, line)
    cols = [Col("description", "Particulars", 265), Col("quantity", "Nos", 50, "r"),
            Col("unit_price", "@", 80, "r"), Col("amount", "Value", 100, "r")]  # fmt: skip
    st = TableStyle(rules=True, cell_money="{n}")
    y = draw_table(c, inv, cols, 50, h - 210, st, lambda: h - 70)
    y = room_for_totals(c, y - 10, lambda: h - 70)
    rows = [
        ("Goods", m(inv.subtotal)),
        (f"VAT @ {inv.tax_rate}%", m(inv.tax)),
        ("To pay", m(printed_total(inv))),
    ]
    draw_totals(c, rows, 340, w - 55, y)
    footer = f"{inv.vendor} · {', '.join(inv.vendor_addr)} · Company No 01234567"
    put(c, w / 2, 50, footer, size=8, align="c")


def layout_stress_ambiguous(c: Canvas, inv: Invoice) -> None:
    """Stress. Day-first slash dates with dot decimals, a quote number beside the invoice number,
    a weak "Ref", ship-to before bill-to, a deposit and balance after the total, $ meaning AUD."""
    w, h = LETTER
    m = lambda d: "$" + fmt_num(d, "us")  # noqa: E731
    put(c, 50, h - 60, inv.vendor, "Helvetica-Bold", 16)
    for i, line in enumerate(inv.vendor_addr):
        put(c, 50, h - 76 - 12 * i, line, size=9)
    put(c, w - 50, h - 60, "TAX INVOICE", "Helvetica-Bold", 14, "r")
    meta = [("Quote No:", f"Q-{inv.number[-4:]}"), ("Invoice No:", inv.number),
            ("Date:", inv.invoice_date.strftime("%d/%m/%Y")), ("Due:", inv.due_date.strftime("%d/%m/%Y"))]  # fmt: skip
    if inv.po:
        meta.append(("Ref:", inv.po))
    for i, (label, value) in enumerate(meta):
        put(c, w - 150, h - 85 - 14 * i, label, "Helvetica-Bold", 9.5, "r")
        put(c, w - 142, h - 85 - 14 * i, value, size=9.5)
    put(c, 50, h - 175, "Ship To:", "Helvetica-Bold", 10)
    for i, line in enumerate(["Warehouse 4", *inv.bill_addr]):
        put(c, 50, h - 189 - 13 * i, line)
    put(c, 250, h - 175, "Bill To:", "Helvetica-Bold", 10)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 250, h - 189 - 13 * i, line)
    cols = [Col("description", "Description", 272), Col("quantity", "Qty", 60, "r"),
            Col("unit_price", "Unit Price", 90, "r"), Col("amount", "Amount", 90, "r")]  # fmt: skip
    st = TableStyle(grid=True, cell_money="${n}")
    y = draw_table(c, inv, cols, 50, h - 265, st, lambda: h - 70)
    y = room_for_totals(c, y - 8, lambda: h - 70, need=160)
    rows = [
        ("Subtotal", m(inv.subtotal)),
        (f"GST ({inv.tax_rate}%)", m(inv.tax)),
        ("Total", m(printed_total(inv))),
    ]
    y = draw_totals(c, rows, w - 220, w - 55, y)
    if inv.deposit:
        paid = [("Deposit Paid", m(inv.deposit)), ("Balance Due", m(inv.total - inv.deposit))]
        draw_totals(c, paid, w - 220, w - 55, y)
    put(c, 50, 60, "All amounts in AUD. Payment within 14 days.", size=8.5)


def layout_stress_table(c: Canvas, inv: Invoice) -> None:
    """Stress. Seller name in letter-spaced capitals, two-digit years, and a discount column so
    that quantity x price does not equal the line amount on discounted rows."""
    w, h = LETTER
    m = lambda d: "$" + fmt_num(d, "us")  # noqa: E731
    spaced = "   ".join(" ".join(word) for word in inv.vendor.upper().split())
    put(c, 50, h - 60, spaced, "Helvetica-Bold", 13)
    put(c, 50, h - 76, " | ".join(inv.vendor_addr), size=8.5)
    meta = [("Invoice Number", inv.number), ("Invoice Date", inv.invoice_date.strftime("%d/%m/%y")),
            ("Due Date", inv.due_date.strftime("%d/%m/%y"))]  # fmt: skip
    for i, (label, value) in enumerate(meta):
        put(c, 50, h - 115 - 14 * i, label, "Helvetica-Bold", 9.5)
        put(c, 150, h - 115 - 14 * i, value, size=9.5)
    put(c, 360, h - 115, "Bill To", "Helvetica-Bold", 9.5)
    for i, line in enumerate([inv.bill_to, *inv.bill_addr]):
        put(c, 360, h - 129 - 13 * i, line, size=9.5)
    cols = [Col("description", "Description", 242), Col("quantity", "Qty", 50, "r"),
            Col("unit_price", "Price", 80, "r"), Col("disc", "Disc", 50, "r"), Col("amount", "Amount", 90, "r")]  # fmt: skip
    st = TableStyle(rules=True, size=9)
    y = draw_table(c, inv, cols, 50, h - 215, st, lambda: h - 70)
    y = room_for_totals(c, y - 10, lambda: h - 70)
    rows = [
        ("Subtotal", m(inv.subtotal)),
        (f"Tax {inv.tax_rate}%", m(inv.tax)),
        ("Total", m(printed_total(inv))),
    ]
    draw_totals(c, rows, w - 220, w - 55, y)


RENDERERS: dict[str, tuple[Callable[[Canvas, Invoice], None], tuple[float, float], str]] = {
    "classic": (layout_classic, LETTER, "USD"),
    "euro": (layout_euro, A4, "EUR"),
    "minimal": (layout_minimal, LETTER, "GBP"),
    "ledger": (layout_ledger, LETTER, "USD"),
    "boxed": (layout_boxed, LETTER, "CAD"),
    "letter": (layout_letter, A4, "EUR"),
    "services": (layout_services, LETTER, "USD"),
    "swiss": (layout_swiss, A4, "CHF"),
    "retail": (layout_retail, LETTER, "USD"),
    "statement": (layout_statement, A4, "EUR"),
    "stress_wording": (layout_stress_wording, A4, "GBP"),
    "stress_ambiguous": (layout_stress_ambiguous, LETTER, "AUD"),
    "stress_table": (layout_stress_table, LETTER, "USD"),
}
ITEM_RANGE = {"ledger": (22, 70), "swiss": (6, 40), "services": (3, 9)}
TAX_RATES = {"classic": ["6", "7.25", "8.25"], "euro": ["19", "7"], "minimal": ["20"],
             "ledger": ["0", "6.5", "8"], "boxed": ["5", "13"], "letter": ["20", "10"],
             "services": ["7", "8.875"], "swiss": ["8.1", "2.6"], "retail": ["6", "7.5"],
             "statement": ["21", "9"], "stress_wording": ["20"], "stress_ambiguous": ["10"],
             "stress_table": ["7", "8.5"]}  # fmt: skip
NUMBER_FORMATS = [
    "INV-{y}-{n:04d}",
    "{n:05d}",
    "RE-{n:06d}",
    "F{y}-{n:03d}",
    "A-{n:04d}-B",
    "{y}/{n:04d}",
]


def make_invoice(layout: str, rng: random.Random) -> Invoice:
    vendor = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)}"
    if rng.random() < 0.35:
        bill_to = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    else:
        bill_to = vendor
        while bill_to == vendor:
            bill_to = f"{rng.choice(NAME_A)} {rng.choice(NAME_B)}"

    def addr() -> list[str]:
        return [
            f"{rng.randint(1, 980)} {rng.choice(STREETS)}",
            f"{rng.choice(CITIES)} {rng.randint(10000, 99899)}",
        ]

    lo, hi = ITEM_RANGE.get(layout, (2, 12))
    items = []
    for _ in range(rng.randint(lo, hi)):
        if layout == "services":
            desc = rng.choice(LONG_ITEMS)
            qty = Decimal(rng.choice(["1", "2", "3.5", "4", "6.25", "8", "12.5", "16", "40"]))
        else:
            desc = rng.choice(ITEMS)
            qty = (
                Decimal(rng.randint(1, 60))
                if rng.random() < 0.85
                else Decimal(rng.choice(["0.5", "1.5", "2.25", "7.5"]))
            )
        price = q2(
            Decimal(
                rng.choice(
                    [
                        rng.uniform(1.5, 60),
                        rng.uniform(5, 120),
                        rng.uniform(90, 900),
                        rng.uniform(900, 2500),
                    ]
                )
            )
        )
        disc = Decimal(0)
        if layout == "stress_table" and rng.random() < 0.4:
            disc = Decimal(rng.choice(["5", "10", "15"]))
        items.append(Item(desc, qty, price, q2(qty * price * (100 - disc) / 100), disc))
    issued = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
    name = vendor.split()[0].lower()
    inv = Invoice(
        layout=layout,
        vendor=vendor,
        vendor_addr=addr(),
        vendor_email=f"accounts@{name}.example",
        number=rng.choice(NUMBER_FORMATS).format(y=issued.year, n=rng.randint(1, 9999)),
        invoice_date=issued,
        due_date=issued + timedelta(days=rng.choice([7, 14, 15, 30, 30, 45, 60])),
        bill_to=bill_to,
        bill_addr=addr(),
        currency=RENDERERS[layout][2],
        items=items,
        tax_rate=Decimal(rng.choice(TAX_RATES[layout])),
        shipping=q2(Decimal(rng.uniform(8, 120)))
        if layout in ("classic", "services", "retail") and rng.random() < 0.25
        else None,
        po=f"PO-{rng.randint(10000, 99999)}" if rng.random() < 0.5 else None,
        customer_no=f"C-{rng.randint(1000, 9999)}" if rng.random() < 0.5 else None,
        order_date=issued - timedelta(days=rng.randint(2, 20)) if rng.random() < 0.5 else None,
    )
    if layout == "stress_ambiguous" and rng.random() < 0.6:
        inv.deposit = q2(inv.total * Decimal("0.3"))
    return inv


def render(inv: Invoice, path: Path) -> None:
    draw, size, _ = RENDERERS[inv.layout]
    c = Canvas(str(path), pagesize=size, invariant=1)
    c.setTitle("Invoice")
    c.setAuthor("synthetic")
    draw(c, inv)
    c.save()


def make_scan(pdf: Path, out: Path) -> None:
    """Rasterise a text PDF into an image-only PDF, like a scanner would."""
    import pymupdf

    with pymupdf.open(pdf) as src, pymupdf.open() as dst:
        for page in src:
            pix = page.get_pixmap(dpi=80, colorspace=pymupdf.csGRAY)
            new = dst.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=pix)
        dst.save(out, deflate=True, no_new_id=True)


def generate(out: Path, per_layout: int = 40, seed: int = 20261120, scans: int = 12,
             layouts: tuple[str, ...] = LAYOUTS) -> list[Path]:  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for layout in layouts:
        # one stream per layout: adding a layout never changes the others
        rng = random.Random(f"{seed}-{layout}")
        for i in range(1, per_layout + 1):
            inv = make_invoice(layout, rng)
            pdf = out / f"{layout}_{i:03d}.pdf"
            render(inv, pdf)
            pdf.with_suffix(".json").write_text(json.dumps(inv.truth(), indent=1), encoding="utf-8")
            made.append(pdf)
    rng = random.Random(f"{seed}-scans")
    for i in range(1, scans + 1):
        inv = make_invoice(rng.choice([x for x in layouts if x in LAYOUTS] or layouts), rng)
        tmp = out / f"_scan_src_{i}.pdf"
        render(inv, tmp)
        pdf = out / f"scan_{i:03d}.pdf"
        make_scan(tmp, pdf)
        tmp.unlink()
        pdf.with_suffix(".json").write_text(
            json.dumps({**inv.truth(), "scanned": True}, indent=1), encoding="utf-8"
        )
        made.append(pdf)
    return made


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", type=Path, default=Path("benchmark/data"))
    p.add_argument("--per-layout", type=int, default=40)
    p.add_argument("--seed", type=int, default=20261120)
    p.add_argument("--scans", type=int, default=12)
    args = p.parse_args()
    made = generate(args.out, args.per_layout, args.seed, args.scans)
    print(f"{len(made)} PDFs written to {args.out}")


if __name__ == "__main__":
    main()
