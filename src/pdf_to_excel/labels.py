"""Label synonyms and the matcher that finds them on a line.

Each synonym carries a strength: how sure we are that this wording means
this field. "Invoice Number" is 1.0, a bare "Reference" is much weaker
because it is often a purchase order or a customer reference.
"""

from __future__ import annotations

from dataclasses import dataclass

from .reader import Line, Word

# field None marks a distractor: a label we recognise so that its value is
# never taken for something else (PO number, order date, tax id ...).
LABELS: dict[str | None, list[tuple[str, float]]] = {
    "invoice_number": [
        ("invoice number", 1.0), ("invoice no", 1.0), ("invoice #", 1.0), ("invoice num", 1.0),
        ("invoice id", 1.0), ("invoice ref", 0.92), ("invoice reference", 0.92),
        ("inv no", 0.96), ("inv #", 0.96), ("inv number", 0.96), ("inv num", 0.96), ("inv", 0.7),
        ("bill number", 0.92), ("bill no", 0.92), ("bill #", 0.92),
        ("document number", 0.88), ("document no", 0.88), ("document #", 0.88),
        ("doc no", 0.85), ("doc number", 0.85),
        ("reference number", 0.8), ("reference no", 0.8), ("reference #", 0.8),
        ("ref no", 0.8), ("ref #", 0.8), ("reference", 0.8), ("ref", 0.7),
        ("number", 0.7), ("no", 0.6), ("#", 0.55), ("invoice", 0.6),
    ],
    "invoice_date": [
        ("invoice date", 1.0), ("date of invoice", 1.0), ("date of issue", 1.0),
        ("issue date", 1.0), ("date issued", 1.0), ("issued on", 0.95), ("issued", 0.92),
        ("billing date", 0.95), ("bill date", 0.95), ("document date", 0.92),
        ("inv date", 0.96), ("dated", 0.9), ("date", 0.88),
    ],
    "due_date": [
        ("payment due date", 1.0), ("due date", 1.0), ("date due", 1.0), ("payment due", 1.0),
        ("due on", 0.96), ("due by", 0.96), ("pay by", 0.96), ("payable by", 0.96),
        ("payment date", 0.85), ("due", 0.9),
    ],
    "bill_to": [
        ("bill to", 1.0), ("billed to", 1.0), ("billing to", 1.0), ("invoice to", 1.0),
        ("invoiced to", 1.0), ("customer name", 1.0), ("client name", 1.0), ("sold to", 0.95),
        ("billing address", 0.9), ("customer", 0.9), ("client", 0.9), ("attention", 0.6),
    ],
    "subtotal": [
        ("subtotal", 1.0), ("sub total", 1.0), ("net amount", 0.95), ("net total", 0.95),
        ("total net", 0.95), ("total excl tax", 1.0), ("total excl vat", 1.0),
        ("total excluding tax", 1.0), ("total excluding vat", 1.0), ("total ex vat", 1.0),
        ("total ex tax", 1.0), ("total before tax", 1.0), ("amount before tax", 1.0),
        ("taxable amount", 0.9), ("net", 0.8),
    ],
    "tax": [
        ("sales tax", 1.0), ("tax amount", 1.0), ("total tax", 1.0), ("vat amount", 1.0),
        ("total vat", 1.0), ("tax", 0.95), ("vat", 0.95), ("gst", 0.95), ("hst", 0.95),
        ("pst", 0.9), ("mwst", 0.95), ("tva", 0.95),
    ],
    "total": [
        ("total amount due", 1.0), ("total due", 1.0), ("amount due", 1.0), ("balance due", 1.0),
        ("grand total", 1.0), ("invoice total", 1.0), ("total incl tax", 1.0),
        ("total incl vat", 1.0), ("total including tax", 1.0), ("total including vat", 1.0),
        ("total inc vat", 1.0), ("total inc tax", 1.0), ("total amount", 0.97),
        ("total payable", 1.0), ("amount payable", 1.0), ("please pay", 0.9), ("total", 0.92),
    ],
    "adjustment": [
        ("shipping and handling", 1.0), ("shipping & handling", 1.0), ("shipping", 1.0),
        ("freight", 1.0), ("delivery", 0.9), ("handling", 0.9), ("discount", 1.0),
    ],
    None: [
        ("po number", 1.0), ("po no", 1.0), ("po #", 1.0), ("po", 1.0), ("p.o", 1.0),
        ("purchase order", 1.0), ("order number", 1.0), ("order no", 1.0), ("order #", 1.0),
        ("order date", 1.0), ("order ref", 1.0), ("ship date", 1.0), ("shipping date", 1.0),
        ("delivery date", 1.0), ("service date", 1.0), ("print date", 1.0),
        ("customer no", 1.0), ("customer number", 1.0), ("customer id", 1.0),
        ("customer #", 1.0), ("customer ref", 1.0), ("client id", 1.0), ("client no", 1.0),
        ("account no", 1.0), ("account number", 1.0), ("account #", 1.0), ("account", 1.0),
        ("tax id", 1.0), ("tax no", 1.0), ("tax number", 1.0), ("tax rate", 1.0),
        ("vat no", 1.0), ("vat number", 1.0), ("vat id", 1.0), ("vat reg", 1.0),
        ("vat reg no", 1.0), ("company no", 1.0), ("registration no", 1.0), ("ein", 1.0),
        ("payment terms", 1.0), ("terms", 1.0), ("phone", 1.0), ("tel", 1.0), ("fax", 1.0),
        ("email", 1.0), ("e mail", 1.0), ("web", 1.0), ("website", 1.0), ("page", 1.0),
        ("ship to", 1.0), ("shipped to", 1.0), ("deliver to", 1.0), ("delivery address", 1.0),
        ("amount paid", 1.0), ("paid", 1.0), ("deposit", 1.0), ("bank", 1.0), ("iban", 1.0),
        ("swift", 1.0), ("bic", 1.0), ("sort code", 1.0), ("routing", 1.0), ("currency", 1.0),
        ("from", 1.0), ("notes", 1.0), ("note", 1.0), ("memo", 1.0), ("salesperson", 1.0),
        ("project", 1.0), ("contact", 1.0), ("prepared by", 1.0),
    ],
}  # fmt: skip

TOTALS_FIELDS = ("subtotal", "tax", "total", "adjustment")

_STRIP = ":.,;()[]*"


def tokens_of(text: str) -> list[str]:
    spaced = text.lower().replace("#", " # ").replace("-", " ").replace("_", " ")
    return [t for t in (p.strip(_STRIP) for p in spaced.split()) if t]


_FLAT: list[tuple[tuple[str, ...], str | None, float]] = sorted(
    (
        (tuple(tokens_of(text)), fld, strength)
        for fld, syns in LABELS.items()
        for text, strength in syns
    ),
    key=lambda e: (-len(e[0]), -e[2]),
)


@dataclass
class LabelMatch:
    field: str | None
    strength: float
    line: Line
    w_start: int  # index of the first label word on the line
    w_end: int  # one past the last label word
    text: str

    @property
    def words(self) -> list[Word]:
        return self.line.words[self.w_start : self.w_end]

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def x1(self) -> float:
        return self.words[-1].x1

    @property
    def starts_cell(self) -> bool:
        """True when the label opens a cell: "Freight  12.00", not "Dunmore Freight"."""
        if self.w_start == 0:
            return True
        prev, first = self.line.words[self.w_start - 1], self.words[0]
        return first.x0 - prev.x1 > 0.9 * max(first.size, 6.0)

    @property
    def colon(self) -> bool:
        return self.words[-1].text.rstrip().endswith(":")


def find_labels(line: Line) -> list[LabelMatch]:
    """All label matches on a line, longest wording first so that
    "Due Date" is never read as a bare "Date"."""
    toks: list[tuple[str, int]] = [
        (t, wi) for wi, w in enumerate(line.words) for t in tokens_of(w.text)
    ]
    claimed: set[int] = set()
    found: list[LabelMatch] = []
    for syn, fld, strength in _FLAT:
        n = len(syn)
        for i in range(len(toks) - n + 1):
            if any(toks[i + k][0] != syn[k] for k in range(n)):
                continue
            word_ids = {toks[i + k][1] for k in range(n)}
            if word_ids & claimed:
                continue
            first, last = min(word_ids), max(word_ids)
            # a label must cover its words completely: "Invoice" inside "Invoice-Date" is not one
            if sum(1 for _, wi in toks if first <= wi <= last) != n:
                continue
            claimed |= word_ids
            found.append(LabelMatch(fld, strength, line, first, last + 1, " ".join(syn)))
    return sorted(found, key=lambda m: m.w_start)
