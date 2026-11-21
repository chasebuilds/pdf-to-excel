"""Label-anchored search for the header fields of an invoice."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from .labels import LabelMatch, find_labels, tokens_of
from .models import Field
from .parsing import CURRENCY_SYMBOLS, ISO_CODES, ParsedDate, parse_date, parse_money
from .reader import Cell, Document, Line, Page, split_cells

TITLE_WORDS = {
    "invoice", "tax invoice", "sales invoice", "commercial invoice", "proforma invoice",
    "pro forma invoice", "statement", "receipt", "bill", "original", "copy", "credit note",
    "service invoice", "vat invoice",
}  # fmt: skip
COMPANY_SUFFIX = re.compile(
    r"\b(inc|llc|ltd|limited|gmbh|corp|corporation|co|company|plc|llp|lp|sa|sarl|bv|ag|kg|pty|"
    r"group|partners|associates|studio|studios|works|labs|supply|supplies|services|solutions|"
    r"consulting|logistics|systems|industries|holdings|agency|collective|trading)\b\.?$",
    re.I,
)
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4}|\d{2})\b")
_PUNCT_ONLY = re.compile(r"^[\W_]+$")


@dataclass
class Candidate:
    cell: Cell
    position: float  # how typical the value position is, 0..1
    how: str  # "right" or "below"
    match: LabelMatch
    rank: int = 0  # 0 is the cell nearest the label


def _label_cut(m: LabelMatch, gap: float) -> bool:
    """Does this later label end the value of the label before it?

    Single plain words ("Total", "Net", "Project") often sit inside a
    company name, so they only cut when they look like a label.
    """
    return len(tokens_of(m.text)) > 1 or m.colon or gap > 0.9 * m.words[0].size


def right_cells(match: LabelMatch, matches: list[LabelMatch]) -> list[Cell]:
    words = match.line.words
    end = len(words)
    for other in matches:
        if other.w_start >= match.w_end:
            gap = words[other.w_start].x0 - words[other.w_start - 1].x1
            if _label_cut(other, gap):
                end = other.w_start
                break
    rest = words[match.w_end : end]
    # drop a stray ":" or "-" after the label, but keep "&" inside "Oakline & Sons"
    while rest and _PUNCT_ONLY.match(rest[0].text):
        rest = rest[1:]
    return split_cells(rest)


def below_cells(
    match: LabelMatch,
    matches: list[LabelMatch],
    page: Page,
    labels_by_line: dict[int, list[LabelMatch]],
) -> list[tuple[Cell, float]]:
    """Cells under a label (stacked layouts), nearest line first."""
    label_h = max(match.words[0].bottom - match.words[0].top, 6.0)
    nxt = [m.x0 for m in matches if m.w_start >= match.w_end]
    band_right = (min(nxt) - 4) if nxt else float("inf")
    out: list[tuple[Cell, float]] = []
    depth = 0
    for ln in page.lines:
        if ln.top <= match.line.top + 1:
            continue
        if ln.top - match.line.bottom > 2.6 * label_h or depth >= 2:
            break
        starts = {m.w_start for m in labels_by_line.get(ln.index, [])}
        hit = False
        for cell in ln.cells:
            aligned = (
                abs(cell.x0 - match.x0) <= 6
                or abs(cell.x1 - match.x1) <= 6
                or abs(cell.mid - (match.x0 + match.x1) / 2) <= 6
            )
            inside = match.x0 - 12 <= cell.x0 <= min(match.x1 + 12, band_right)
            if not (aligned or inside) or cell.x0 >= band_right:
                continue
            if ln.words.index(cell.words[0]) in starts:
                return (
                    out  # the column under the label holds another label, so no value lives there
                )
            score = (0.94 if aligned else 0.8) * (1.0 if depth == 0 else 0.9)
            out.append((cell, score))
            hit = True
        if hit:
            depth += 1
    return out


class FieldFinder:
    def __init__(self, doc: Document, skip_lines: set[int], decimal_comma: bool):
        self.doc = doc
        self.decimal_comma = decimal_comma
        self.labels_by_line: dict[int, list[LabelMatch]] = {}
        self.page_of: dict[int, Page] = {}
        for page in doc.pages:
            for ln in page.lines:
                self.page_of[ln.index] = page
                if ln.index not in skip_lines:
                    self.labels_by_line[ln.index] = find_labels(ln)
        self.day_first = self._infer_day_first()

    # ---- candidates -------------------------------------------------

    def matches(self, name: str) -> list[tuple[LabelMatch, list[LabelMatch]]]:
        return [(m, ms) for ms in self.labels_by_line.values() for m in ms if m.field == name]

    def candidates(self, name: str, same_line_any_gap: bool = False) -> list[Candidate]:
        out: list[Candidate] = []
        for m, ms in self.matches(name):
            cells = right_cells(m, ms)
            if cells:
                gap = cells[0].x0 - m.x1
                pos = 1.0 if same_line_any_gap or gap <= 70 else 0.93 if gap <= 220 else 0.8
                out.extend(Candidate(c, pos, "right", m, i) for i, c in enumerate(cells))
            if m.text == "invoice":
                continue  # the document title: never read the block under it as a number
            under = below_cells(m, ms, self.page_of[m.line.index], self.labels_by_line)
            for i, (cell, score) in enumerate(under):
                # with something on the same line, a value underneath is the less likely reading
                out.append(Candidate(cell, score * (0.9 if cells else 1.0), "below", m, i))
        return out

    def _infer_day_first(self) -> bool | None:
        votes: set[bool] = set()
        for ln in self.doc.lines:
            for a, b, _ in _NUMERIC_DATE.findall(ln.text):
                if int(a) > 12 >= int(b):
                    votes.add(True)
                elif int(b) > 12 >= int(a):
                    votes.add(False)
        return votes.pop() if len(votes) == 1 else None

    # ---- invoice number ---------------------------------------------

    def invoice_number(self) -> Field:
        scored: list[tuple[float, str, Candidate]] = []
        for c in self.candidates("invoice_number"):
            if c.rank > 0:
                continue  # only the cell right after or right under the label
            words = [w.text for w in c.cell.words]
            keep = []
            for w in words[:3]:
                # "INV 2026 0042" is one number; "RE-1715 (continued)" is a number and a remark
                if not (re.search(r"\d", w) or re.fullmatch(r"[A-Z]{1,4}[-/.]?", w)):
                    break
                keep.append(w)
            text = " ".join(keep)
            text = re.sub(r"^(?:no\.?|number|#|:)\s*", "", text, flags=re.I).strip(" :#")
            if not re.search(r"\d", text) or not 2 <= len(text) <= 30:
                continue
            if parse_date(text) or re.search(r"[$€£%]", text):
                continue
            digits = sum(ch.isdigit() for ch in text)
            pattern = 1.0 if digits >= 3 else 0.8
            if len(words) > 1 and not re.fullmatch(r"[A-Z0-9][A-Z0-9 /.-]*", text):
                pattern *= 0.85  # several words with lower case: may be prose, not a number
            scored.append((0.97 * c.match.strength * c.position * pattern, text, c))
        return self._pick(scored, "invoice number", bump_sole=True)

    def _pick(
        self, scored: list[tuple[float, Any, Candidate]], what: str, bump_sole: bool = False
    ) -> Field:
        if not scored:
            return Field(reasons=[f"no {what} label with a usable value"])
        scored.sort(key=lambda s: -s[0])
        score, value, cand = scored[0]
        f = Field(
            value=value,
            confidence=round(score, 3),
            page=cand.match.line.page,
            method=f"label '{cand.match.text}' ({cand.how})",
            raw=cand.cell.text,
        )
        if cand.match.strength < 0.9:
            f.reasons.append(f"weak label '{cand.match.text}'")
        if cand.how == "below":
            f.reasons.append("value taken from the line under the label")
        rivals = [s for s in scored[1:] if s[1] != value and s[2].match is not cand.match]
        agree = [s for s in scored[1:] if s[1] == value and s[2].match is not cand.match]
        if rivals and rivals[0][0] >= score - 0.12:
            f.lower(0.8, f"another candidate scored close: '{rivals[0][1]}'")
        elif agree:
            f.raise_to(min(0.97, score + 0.05))
        elif bump_sole and not rivals:
            f.raise_to(min(0.97, score + 0.04))
        return f

    # ---- dates ------------------------------------------------------

    def _date_candidates(self, name: str) -> list[tuple[float, ParsedDate, Candidate]]:
        out = []
        for c in self.candidates(name):
            pd = parse_date(c.cell.text, self.day_first)
            if pd:
                out.append((0.97 * c.match.strength * c.position, pd, c))
        return sorted(out, key=lambda s: -s[0])

    def dates(self) -> tuple[Field, Field]:
        inv_c, due_c = self._date_candidates("invoice_date"), self._date_candidates("due_date")
        fields: list[Field] = []
        parsed: list[ParsedDate | None] = []
        for cands, what in ((inv_c, "invoice date"), (due_c, "due date")):
            if not cands:
                fields.append(Field(reasons=[f"no {what} label with a parsable date"]))
                parsed.append(None)
                continue
            f = self._pick([(s, pd.value, c) for s, pd, c in cands], what)
            parsed.append(cands[0][1])
            if cands[0][1].two_digit_year:
                f.lower(0.95, "two-digit year")
            fields.append(f)
        self._settle_day_month(fields, parsed)
        inv, due = fields
        if isinstance(inv.value, date) and isinstance(due.value, date):
            days = (due.value - inv.value).days
            if days < 0:
                inv.lower(0.7, "due date is before invoice date")
                due.lower(0.7, "due date is before invoice date")
            elif days > 366:
                due.lower(0.75, "due date is more than a year after invoice date")
            else:
                for f in (inv, due):
                    if f.confidence >= 0.8:
                        f.raise_to(min(0.99, f.confidence + 0.04))
        return inv, due

    def _settle_day_month(self, fields: list[Field], parsed: list[ParsedDate | None]) -> None:
        """03/04/2026 has two readings. Use the invoice/due pair to choose, else say so."""
        amb = [i for i, p in enumerate(parsed) if p and p.ambiguous]
        if not amb:
            return
        options = [
            ([p.value, p.alt] if p and p.ambiguous else [p.value if p else None]) for p in parsed
        ]
        valid = []
        for swap in (False, True):  # the same convention applies to both dates
            pair = [opts[1] if swap and len(opts) > 1 else opts[0] for opts in options]
            if pair[0] and pair[1] and 0 <= (pair[1] - pair[0]).days <= 120:
                valid.append((swap, pair))
        if len(valid) == 1:
            swap, pair = valid[0]
            for i in amb:
                fields[i].value = pair[i]
                fields[i].lower(0.9, "day/month order inferred from the invoice/due date pair")
            return
        if self.decimal_comma:
            for i in amb:
                fields[i].value = p.alt if (p := parsed[i]) else None
                fields[i].lower(0.85, "day/month order inferred from comma decimals (day first)")
            return
        for i in amb:
            fields[i].lower(0.62, "ambiguous day/month order, month-first assumed")

    # ---- bill to ----------------------------------------------------

    def bill_to(self) -> Field:
        scored = []
        for c in self.candidates("bill_to"):
            text = c.cell.text.strip(" :,")
            if c.rank > 0:
                continue  # the name is the first cell; later ones are the address
            if not re.search(r"[A-Za-z]{2}", text) or len(text) > 80:
                continue
            if text.lower() in TITLE_WORDS or parse_date(text) or "@" in text:
                continue
            if c.how == "right" and c.cell.x0 - c.match.x1 > 70:
                continue  # far to the right is the other column of the header, not the name
            shape = 0.85 if re.match(r"^\d", text) else 1.0
            scored.append((0.97 * c.match.strength * c.position * shape, text, c))
        return self._pick(scored, "bill-to")

    # ---- vendor -----------------------------------------------------

    def vendor(self, bill_to: str | None, table_start: int | None = None) -> Field:
        page = next((p for p in self.doc.pages if p.lines), None)
        if not page:
            return Field(reasons=["no text on the first page"])
        sizes = sorted(w.size for ln in page.lines for w in ln.words)
        body = sizes[len(sizes) // 2]
        pool: list[tuple[Cell, Line]] = []
        for ln in page.lines:
            if ln.top > page.height * 0.45 or (table_start is not None and ln.index >= table_start):
                break
            # a cell that opens with a label is a label; "Alder Freight" merely contains one
            label_starts = {
                id(m.words[0]) for m in self.labels_by_line.get(ln.index, []) if m.text != "invoice"
            }
            pool.extend((c, ln) for c in ln.cells if id(c.words[0]) not in label_starts)

        def name_like(cell: Cell) -> bool:
            t = cell.text.strip()
            low = re.sub(r"[^a-z ]", "", t.lower()).strip()
            tokens = t.split()
            # mostly one-letter pieces: letter-spaced type, or words broken apart by a tilted page
            scattered = len(tokens) > 2 and sum(len(x) == 1 for x in tokens) * 2 > len(tokens)
            return (
                not scattered
                and bool(re.search(r"[A-Za-z]{2}", t))
                and low not in TITLE_WORDS
                and not re.match(r"^[\d(+#]", t)
                and not re.search(r"@|www\.|https?:|\d{4,}", t)
                and not parse_date(t)
                and t != (bill_to or "")
                and len(t) <= 60
            )

        named = [(c, ln) for c, ln in pool if name_like(c)]
        if not named:
            return Field(reasons=["no company name found at the top of the first page"])
        votes: Counter[str] = Counter()
        how: dict[str, list[str]] = {}

        def vote(cell: Cell, why: str) -> None:
            votes[cell.text.strip()] += 1
            how.setdefault(cell.text.strip(), []).append(why)

        biggest = max(named, key=lambda cl: (round(cl[0].words[0].size, 1), -cl[1].top))
        if biggest[0].words[0].size >= body * 1.15:
            vote(biggest[0], "largest text")
        vote(min(named, key=lambda cl: (round(cl[1].top), cl[0].x0))[0], "first line")
        suffixed = [c for c, _ in named if COMPANY_SUFFIX.search(c.text.strip())]
        if suffixed:
            vote(suffixed[0], "company suffix")
        order = {"largest text": 0, "company suffix": 1, "first line": 2}
        name, n = min(votes.items(), key=lambda kv: (-kv[1], min(order[w] for w in how[kv[0]])))
        conf = {3: 0.96, 2: 0.9}.get(
            n, {"largest text": 0.74, "company suffix": 0.7, "first line": 0.55}[how[name][0]]
        )
        f = Field(
            value=name, confidence=conf, page=page.number, method=" + ".join(how[name]), raw=name
        )
        if n == 1:
            f.reasons.append(f"only one signal ({how[name][0]}); invoices rarely label the seller")
        if len(votes) > 1 and n < 3:
            others = [k for k in votes if k != name]
            f.reasons.append(f"other candidate: '{others[0]}'")
        return f

    # ---- currency ---------------------------------------------------

    def currency(self) -> Field:
        codes: Counter[str] = Counter()
        symbols: Counter[str] = Counter()
        page_seen: dict[str, int] = {}
        for ln in self.doc.lines:
            for w in ln.words:
                t = w.text.strip("()[]:,.")
                if t in ISO_CODES:
                    codes[t] += 1
                    page_seen.setdefault(t, ln.page)
            for cell in ln.cells:
                m = parse_money(cell.text, self.decimal_comma)
                mark = re.search(r"US\$|CA\$|C\$|AU\$|A\$|NZ\$|[$€£¥₹]", cell.text) if m else None
                if mark:
                    symbols[mark[0]] += 1
                    page_seen.setdefault(mark[0], ln.page)
        if not codes and not symbols:
            return Field(reasons=["no currency code or symbol found"])
        if codes:
            code, _ = codes.most_common(1)[0]
            f = Field(
                value=code, confidence=0.93, page=page_seen[code], method="ISO code", raw=code
            )
            if len(codes) > 1:
                f.lower(0.6, f"several currency codes present: {', '.join(sorted(codes))}")
            if symbols:
                sym = symbols.most_common(1)[0][0]
                implied = CURRENCY_SYMBOLS[sym]
                if implied == code or (
                    sym == "$" and code in ("USD", "CAD", "AUD", "NZD", "SGD", "HKD", "MXN")
                ):
                    f.raise_to(0.97)
                    f.method = "ISO code + symbol"
                else:
                    f.lower(0.6, f"symbol {sym} does not match code {code}")
            return f
        sym, _ = symbols.most_common(1)[0]
        f = Field(
            value=CURRENCY_SYMBOLS[sym],
            confidence=0.93,
            page=page_seen[sym],
            method=f"symbol {sym}",
            raw=sym,
        )
        if sym == "$":
            f.confidence = 0.85
            f.reasons.append("bare $ with no currency code: USD assumed")
        if len(symbols) > 1:
            f.lower(0.6, "several currency symbols present")
        return f
