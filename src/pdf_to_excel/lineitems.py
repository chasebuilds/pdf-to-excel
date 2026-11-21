"""Find the line item table and read its rows.

Two methods. The main one finds the header row by its wording and maps
each data cell to a column. The fallback has no header: it keeps rows
whose last three numbers satisfy quantity x price = amount. The same
arithmetic also settles which numeric column is which when the header
wording is unclear.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import permutations

from .labels import TOTALS_FIELDS, find_labels, tokens_of
from .models import LineItem
from .parsing import parse_money, parse_number
from .reader import Cell, Document, Line, split_cells

HEADER_SYNONYMS: dict[str, list[str]] = {
    "description": [
        "description", "item", "items", "details", "service", "services", "product", "products",
        "particulars", "item description", "description of services", "work performed", "task",
        "description of goods", "goods", "activity",
    ],
    "quantity": ["qty", "quantity", "units", "hours", "hrs", "count", "pcs", "days", "qty ordered"],
    "unit_price": [
        "unit price", "price", "rate", "unit cost", "each", "price each", "cost", "unit rate",
        "hourly rate", "price per unit", "unit amount", "list price",
    ],
    "amount": [
        "amount", "total", "line total", "net", "ext price", "extended", "extended price",
        "net amount", "line amount", "sum", "total price", "net price", "value",
    ],
    "other": [
        "#", "no", "pos", "code", "sku", "item no", "item #", "item code", "part no", "unit", "uom",
        "tax", "vat", "vat %", "tax %", "date", "discount", "disc", "ref", "line",
    ],
}  # fmt: skip
_ROLE_BY_TOKENS = {
    tuple(tokens_of(s)): role for role, syns in HEADER_SYNONYMS.items() for s in syns
}
_MAX_SYN = max(len(k) for k in _ROLE_BY_TOKENS)
NUMERIC_ROLES = ("quantity", "unit_price", "amount")
_QTY_UNIT = re.compile(r"^(.*\d)\s*(?:x|hrs?|h|pcs?|ea|units?|days?)\.?$", re.I)
CENT = Decimal("0.011")


@dataclass
class Column:
    role: str
    x0: float
    x1: float


@dataclass
class Table:
    items: list[LineItem] = field(default_factory=list)
    used_lines: set[int] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)
    method: str = ""


def _cell_roles(cell: Cell) -> list[Column] | None:
    """Roles for a header cell. A cell may hold two headers that sit close together."""
    toks = [(tuple(tokens_of(re.sub(r"\(.*?\)", "", w.text))), w) for w in cell.words]
    toks = [(t, w) for t, w in toks if t]
    cols: list[Column] = []
    i = 0
    while i < len(toks):
        for n in range(min(_MAX_SYN, len(toks) - i), 0, -1):
            flat = tuple(t for ts, _ in toks[i : i + n] for t in ts)
            if flat in _ROLE_BY_TOKENS:
                cols.append(Column(_ROLE_BY_TOKENS[flat], toks[i][1].x0, toks[i + n - 1][1].x1))
                i += n
                break
        else:
            return None
    return cols or None


def find_header(line: Line) -> list[Column] | None:
    cols: list[Column] = []
    for cell in line.cells:
        roles = _cell_roles(cell)
        if roles is None:
            return None
        cols.extend(roles)
    names = [c.role for c in cols]
    numeric = {r for r in names if r in NUMERIC_ROLES}
    if names.count("description") == 1 and "amount" in numeric and len(numeric) >= 2:
        return cols
    return None


def _is_totals_line(line: Line, decimal_comma: bool) -> bool:
    # a totals row is a label that opens a cell plus one amount; a seller called
    # "Dunmore Freight" in a page header, or an item called "Delivery", is neither
    if not any(m.field in TOTALS_FIELDS and m.starts_cell for m in find_labels(line)):
        return False
    numbers = sum(1 for c in line.cells if parse_money(c.text, decimal_comma))
    return numbers == 1


def _qty(text: str, decimal_comma: bool) -> Decimal | None:
    m = _QTY_UNIT.match(text.strip())
    return parse_number(m[1] if m else text, decimal_comma)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return min(a1, b1) - max(a0, b0)


def _assign(cells: list[Cell], bands: list[Column]) -> dict[int, list[Cell]]:
    out: dict[int, list[Cell]] = {}
    for cell in cells:
        best, best_score = None, 0.0
        for i, b in enumerate(bands):
            ov = _overlap(cell.x0, cell.x1, b.x0, b.x1)
            score = ov if ov > 0 else -min(abs(cell.x0 - b.x1), abs(cell.x1 - b.x0)) - 1e-6
            if best is None or score > best_score:
                best, best_score = i, score
        if best is not None and best_score > -15:
            out.setdefault(best, []).append(cell)
    return out


def _learn_bands(header: list[Column], rows: list[list[Cell]]) -> tuple[list[Column], bool]:
    """Column x-ranges from the data itself.

    Header text and numbers are often aligned differently (header left,
    numbers right), so matching a number to the nearest header is wrong
    surprisingly often. Rows with one cell per header are mapped by order
    and their extents define each column.
    """
    full = [r for r in rows if len(r) == len(header)]
    if len(full) >= max(1, len(rows) // 2):
        bands = [
            Column(h.role, min(r[i].x0 for r in full), max(r[i].x1 for r in full))
            for i, h in enumerate(header)
        ]
        return bands, True
    # fall back to the header positions, widened to the midpoints between headers
    bands = []
    for i, h in enumerate(header):
        left = (header[i - 1].x1 + h.x0) / 2 if i else h.x0 - 40
        right = (h.x1 + header[i + 1].x0) / 2 if i + 1 < len(header) else h.x1 + 40
        bands.append(Column(h.role, left, right))
    return bands, False


def _row_ok(q: Decimal | None, p: Decimal | None, a: Decimal | None) -> bool | None:
    if q is None or p is None or a is None:
        return None
    return abs(q * p - a) <= CENT


def _read_rows(
    lines: list[Line], bands: list[Column], roles: list[str], decimal_comma: bool, base: float
) -> tuple[list[LineItem], set[int]]:
    items: list[LineItem] = []
    used: set[int] = set()
    desc_i = roles.index("description")
    last: Line | None = None
    for ln in lines:
        mapped = _assign(ln.cells, bands)
        text = {
            roles[i]: " ".join(c.text for c in cs)
            for i, cs in mapped.items()
            if roles[i] != "other"
        }
        amount = parse_money(text.get("amount", ""), decimal_comma)
        price = parse_money(text.get("unit_price", ""), decimal_comma)
        qty = _qty(text.get("quantity", ""), decimal_comma) if text.get("quantity") else None
        desc = text.get("description", "").strip()
        if amount and desc and (qty is not None or price or "quantity" not in roles):
            items.append(
                LineItem(
                    desc, qty, price.amount if price else None, amount.amount, base, [], ln.page
                )
            )
            used.add(ln.index)
            last = ln
        elif (
            items
            and last is not None
            and set(mapped) == {desc_i}
            and ln.page == last.page
            and ln.top - last.bottom <= 0.9 * (last.bottom - last.top)
        ):
            items[-1].description += " " + desc  # wrapped description
            used.add(ln.index)
            last = ln
    return items, used


def _score_rows(items: list[LineItem]) -> float:
    checks = [_row_ok(i.quantity, i.unit_price, i.amount) for i in items]
    known = [c for c in checks if c is not None]
    return sum(known) / len(known) if known else 0.0


def _with_header(doc: Document, decimal_comma: bool) -> Table | None:
    lines = doc.lines
    start = next(((i, h) for i, ln in enumerate(lines) if (h := find_header(ln))), None)
    if not start:
        return None
    hi, header = start
    table = Table(method="header row", used_lines={lines[hi].index})
    body: list[Line] = []
    for ln in lines[hi + 1 :]:
        again = find_header(ln)
        if again and [c.role for c in again] == [c.role for c in header]:
            table.used_lines.add(ln.index)  # header repeated on a later page
            continue
        if _is_totals_line(ln, decimal_comma):
            break
        body.append(ln)
    numeric_rows = [
        ln.cells
        for ln in body
        if sum(1 for c in ln.cells if parse_money(c.text, decimal_comma)) >= 2
    ]
    bands, by_order = _learn_bands(header, numeric_rows)
    roles = [b.role for b in bands]
    items, used = _read_rows(body, bands, roles, decimal_comma, 0.92 if by_order else 0.84)
    if not by_order:
        table.reasons.append("columns matched to header positions, not confirmed by row shape")

    # Let arithmetic settle the numeric columns when the header wording did not.
    num_idx = [i for i, r in enumerate(roles) if r in NUMERIC_ROLES]
    if items and len(num_idx) == 3 and _score_rows(items) < 0.6:
        for perm in permutations(NUMERIC_ROLES):
            trial = list(roles)
            for i, r in zip(num_idx, perm, strict=True):
                trial[i] = r
            t_items, t_used = _read_rows(body, bands, trial, decimal_comma, 0.86)
            if t_items and len(t_items) >= len(items) and _score_rows(t_items) >= 0.9:
                items, used = t_items, t_used
                table.reasons.append("numeric column roles settled by quantity x price = amount")
                break
    table.items = items
    table.used_lines |= used
    return table


def _without_header(doc: Document, decimal_comma: bool) -> Table:
    """No recognisable header: keep rows whose trailing numbers multiply out."""
    table = Table(method="arithmetic rows")
    for ln in doc.lines:
        cells = ln.cells
        if len(cells) < 4 or _is_totals_line(ln, decimal_comma):
            continue
        tail = cells[-3:]
        nums = [m for c in tail if (m := parse_money(c.text, decimal_comma))]
        if len(nums) < 3:
            continue
        a, b, c = (n.amount for n in nums)
        desc = " ".join(x.text for x in cells[:-3])
        desc = re.sub(r"^\d{1,3}\s+", "", desc)
        if abs(a * b - c) <= CENT and re.search(r"[A-Za-z]", desc):
            qty, price = (a, b) if not nums[0].has_decimals or nums[1].has_decimals else (b, a)
            table.items.append(LineItem(desc, qty, price, c, 0.8, [], ln.page))
            table.used_lines.add(ln.index)
    if table.items:
        table.reasons.append("no table header recognised; rows found by quantity x price = amount")
    return table


def extract_line_items(doc: Document, decimal_comma: bool) -> Table:
    table = _with_header(doc, decimal_comma)
    if table is None or not table.items:
        fallback = _without_header(doc, decimal_comma)
        if table is not None:
            fallback.used_lines |= table.used_lines
        table = fallback
    if not table.items:
        table.reasons.append("no line item table found")
    for item in table.items:
        q, p, a = item.quantity, item.unit_price, item.amount
        if q is None or p is None or a is None:
            item.confidence = min(item.confidence, 0.7)
            missing = "quantity" if q is None else "unit price"
            item.reasons.append(f"{missing} not found, row cannot be checked")
        elif _row_ok(q, p, a):
            item.confidence = min(0.99, item.confidence + 0.05)
        else:
            item.confidence = 0.5
            item.reasons.append(f"quantity x unit price = {q * p:.2f}, not {a}")
    return table


__all__ = ["Table", "extract_line_items", "find_header", "split_cells"]
