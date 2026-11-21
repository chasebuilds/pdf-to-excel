"""Generate the synthetic invoices, run the extractor, score it, write BENCHMARK.md.

    python -m benchmark.run_benchmark                 # full run, writes BENCHMARK.md
    python -m benchmark.run_benchmark --tuning-only   # development loop, prints only

The model fallback is never used here: every number comes from the
deterministic code path.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import time
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

from benchmark.generate import (
    HELD_OUT_LAYOUTS,
    LAYOUTS,
    ROUND1_HELD_OUT,
    STRESS_LAYOUTS,
    TUNING_LAYOUTS,
    generate,
)
from pdf_to_excel import DEFAULT_THRESHOLD, InvoiceResult, extract_invoice
from pdf_to_excel.models import SCALAR_FIELDS
from pdf_to_excel.workbook import status_of

ROOT = Path(__file__).resolve().parent.parent
MONEY = ("subtotal", "tax", "total")
STRESS_NOTES = {
    "stress_wording": "No familiar wording: `Our Ref`, `Raised`, `Settle by`, a `Particulars / Nos / @ / Value` table, `Goods` and `To pay` for the totals. The customer block has no label and the seller is named only in the footer.",
    "stress_ambiguous": "Day-first slash dates with dot decimals (nothing on the page says which order), a quote number next to the invoice number, a ship-to block before the bill-to block, a deposit and balance after the total, and `$` that means AUD.",
    "stress_table": "Seller name in letter-spaced capitals, two-digit years, and a discount column, so quantity x price does not equal the amount on discounted rows.",
}


def norm(name: str, value: object) -> object:
    if value is None:
        return None
    if name in MONEY:
        return Decimal(str(value))
    if isinstance(value, date):
        return value.isoformat()
    return " ".join(str(value).split()).casefold()


def line_key(item: dict | object) -> tuple:
    get = item.get if isinstance(item, dict) else lambda k: getattr(item, k)
    dec = lambda v: None if v is None else Decimal(str(v))  # noqa: E731
    return (
        " ".join(str(get("description")).split()).casefold(),
        dec(get("quantity")),
        dec(get("unit_price")),
        dec(get("amount")),
    )


def score(truth: dict, result: InvoiceResult, threshold: float) -> dict:
    fields = {}
    for name in SCALAR_FIELDS:
        f = result.get(name)
        fields[name] = {
            "correct": norm(name, f.value) == norm(name, truth[name]),
            "flagged": f.confidence < threshold,
            "conf": f.confidence,
            "extracted": f.value is not None,
            "got": f.value,
            "want": truth[name],
        }
    want = Counter(line_key(i) for i in truth["line_items"])
    got = Counter(line_key(i) for i in result.line_items)
    return {
        "fields": fields,
        "rows_truth": sum(want.values()),
        "rows_matched": sum((want & got).values()),
        "rows_extra": sum((got - want).values()),
        "table_exact": want == got,
        "table_flagged": bool(result.line_items_reasons)
        or not result.line_items
        or any(i.confidence < threshold for i in result.line_items),
    }


def kind_of(layout: str) -> str:
    if layout in STRESS_LAYOUTS:
        return "stress"
    if layout in HELD_OUT_LAYOUTS:
        return "**held out**"
    return "tuning (held out in round 1)" if layout in ROUND1_HELD_OUT else "tuning"


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def frac(a: int, b: int) -> str:
    return f"{pct(a, b)} ({a}/{b})"


def acc(rs: list[dict], names=SCALAR_FIELDS) -> tuple[int, int]:
    cells = [r["score"]["fields"][n] for r in rs for n in names]
    return sum(c["correct"] for c in cells), len(cells)


def outcomes(rs: list[dict], names=SCALAR_FIELDS) -> Counter:
    c: Counter = Counter()
    for r in rs:
        for n in names:
            cell = r["score"]["fields"][n]
            c[
                "correct" if cell["correct"] else "wrong",
                "flagged" if cell["flagged"] else "confident",
            ] += 1
    return c


def build_report(rows: list[dict], scans: list[dict], meta: dict) -> str:
    by_layout: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_layout[r["layout"]].append(r)
    standard = [r for r in rows if r["layout"] in LAYOUTS]
    stress = [r for r in rows if r["layout"] in STRESS_LAYOUTS]
    groups = {
        f"Standard layouts ({len({r['layout'] for r in standard})})": standard,
        "of which tuning layouts": [r for r in standard if r["layout"] in TUNING_LAYOUTS],
        "of which held-out layouts": [r for r in standard if r["layout"] in HELD_OUT_LAYOUTS],
        "Stress layouts (built to be hard, never tuned on)": stress,
    }

    out: list[str] = []
    w = out.append

    def headline_table(sets: dict[str, list[dict]]) -> None:
        w(
            "| Set | Invoices | Header fields correct | Wrong values passed as confident | Wrong values caught by review |"
        )
        w("|---|---|---|---|---|")
        for label, rs in sets.items():
            if not rs:
                continue
            a, n = acc(rs)
            o = outcomes(rs)
            wrong = o["wrong", "flagged"] + o["wrong", "confident"]
            caught = frac(o["wrong", "flagged"], wrong) if wrong else "no wrong values"
            w(
                f"| {label} | {len(rs)} | {frac(a, n)} | {frac(o['wrong', 'confident'], n)} | {caught} |"
            )

    def cost_table(rs: list[dict]) -> None:
        o = outcomes(rs)
        n = sum(o.values())
        w("| Outcome | Fields | Share |")
        w("|---|---|---|")
        for key, label in [
            (("correct", "confident"), "Correct, passed as confident"),
            (
                ("correct", "flagged"),
                "Correct, but sent to review anyway (extra checking for the client)",
            ),
            (("wrong", "flagged"), "Wrong or missing, sent to review (caught)"),
            (("wrong", "confident"), "Wrong, passed as confident (missed)"),
        ]:
            w(f"| {label} | {o[key]} | {pct(o[key], n)} |")

    def missed_table(rs: list[dict]) -> None:
        missed: dict[tuple[str, str], list[tuple[str, object, object]]] = defaultdict(list)
        for r in rs:
            for n, c in r["score"]["fields"].items():
                if not c["correct"] and not c["flagged"]:
                    missed[r["layout"], n].append((r["file"], c["got"], c["want"]))
        if not missed:
            w("None in this run.")
            return
        w("| Layout | Field | Count | Example file | Extracted | Truth |")
        w("|---|---|---|---|---|---|")
        for (layout, name), items in sorted(missed.items()):
            file, got, want = items[0]
            w(f"| {layout} | {name} | {len(items)} | {file} | {got} | {want} |")

    w("# Benchmark")
    w("")
    w(
        "Written by `benchmark/run_benchmark.py` from a real run. Do not edit by hand: rerun the script."
    )
    w("")
    w(
        f"- Invoices scored: **{len(rows)}** text PDFs ({len(standard)} in {len({r['layout'] for r in standard})} standard layouts, "
        f"{len(stress)} in {len({r['layout'] for r in stress})} stress layouts), plus {len(scans)} scanned (image-only) PDFs"
    )
    w(
        f"- Pages read: {meta['pages']}, line items in ground truth: {sum(r['score']['rows_truth'] for r in rows)}"
    )
    w(f"- Generator seed: `{meta['seed']}`, confidence threshold: `{meta['threshold']}`")
    w("- Local model fallback: **off** (all numbers are from deterministic code)")
    w(
        f"- Python {meta['python']}, pdfplumber {meta['pdfplumber']}, {meta['seconds_per_pdf']:.2f} s per PDF on the test machine"
    )
    w(
        '- All data is synthetic. See the caveats under "How the split works" before quoting these numbers.'
    )
    w("")
    w("## Headline")
    w("")
    w(
        "A field counts as correct only on an exact match with the ground truth (text compared without case or extra spaces, dates and amounts compared as values). "
        'A missing value counts as wrong. "Passed as confident" means the confidence was at or above the threshold, so the value did not go to the Review sheet.'
    )
    w("")
    headline_table(groups)
    w("")
    o_std, o_str = outcomes(standard), outcomes(stress)
    w(
        f"**Confident-but-wrong rate: {frac(o_std['wrong', 'confident'], sum(o_std.values()))} of header fields on the standard layouts, "
        f"{frac(o_str['wrong', 'confident'], sum(o_str.values()))} on the stress layouts.** "
        "This is the number that matters most: a wrong value that was not sent to the Review sheet."
    )
    w("")
    w("## What the review sheet costs")
    w("")
    w("Standard layouts:")
    w("")
    cost_table(standard)
    if stress:
        w("")
        w("Stress layouts:")
        w("")
        cost_table(stress)
    w("")
    w("## Accuracy by field")
    w("")
    w(
        "| Field | Standard | Tuning | Held out | Stress | Wrong and confident (standard) | Wrong and confident (stress) |"
    )
    w("|---|---|---|---|---|---|---|")
    for name in SCALAR_FIELDS:
        cells = " | ".join(frac(*acc(rs, (name,))) for rs in groups.values())
        w(
            f"| {name} | {cells} | {outcomes(standard, (name,))['wrong', 'confident']} | "
            f"{outcomes(stress, (name,))['wrong', 'confident']} |"
        )
    w("")
    w("## Precision and recall by field")
    w("")
    w(
        "Precision: of the values the tool wrote down, the share that were right. "
        "Recall: of the values printed on the invoices, the share the tool got right. "
        "A blank lowers recall but not precision, which is the trade this tool makes on purpose: it leaves a field empty and flags it before it guesses."
    )
    w("")
    w("| Field | Precision, standard | Recall, standard | Precision, stress | Recall, stress |")
    w("|---|---|---|---|---|")

    def pr(rs: list[dict], name: str) -> tuple[str, str]:
        cells = [r["score"]["fields"][name] for r in rs]
        right = sum(c["correct"] and c["extracted"] for c in cells)
        return frac(right, sum(c["extracted"] for c in cells)), frac(right, len(cells))

    for name in SCALAR_FIELDS:
        w(f"| {name} | {' | '.join(pr(standard, name))} | {' | '.join(pr(stress, name))} |")
    w("")
    w("## Whole documents")
    w("")
    w(
        "An invoice counts here only when all nine header fields and every line item are exactly right."
    )
    w("")
    w(
        "| Set | Invoices exactly right | of those, passed with status OK | Invoices with any error passed with status OK |"
    )
    w("|---|---|---|---|")
    for label, rs in groups.items():
        if not rs:
            continue
        exact = [r for r in rs if r["exact"]]
        wrong_ok = sum(1 for r in rs if not r["exact"] and r["status"] == "OK")
        w(
            f"| {label} | {frac(len(exact), len(rs))} | {sum(1 for r in exact if r['status'] == 'OK')} | "
            f"{wrong_ok} of {len(rs) - len(exact)} |"
        )
    w("")
    w("## What a confidence score means here")
    w("")
    w(
        "Confidence is a ranking score built from hand-set weights, not a probability. "
        "This table is the evidence for what a band is worth: every header field from every invoice in this run (standard and stress together), grouped by its score."
    )
    w("")
    w("| Confidence band | Fields | Right | Share right | Wrong |")
    w("|---|---|---|---|---|")
    cells = [c for r in rows for c in r["score"]["fields"].values()]
    for lo, hi in [(0.0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 1.01)]:
        band = [c for c in cells if lo <= c["conf"] < hi]
        right = sum(c["correct"] for c in band)
        label = f"{lo:.2f} to {min(hi, 1.0):.2f}" + (" (review)" if hi <= meta["threshold"] else "")
        w(f"| {label} | {len(band)} | {right} | {pct(right, len(band))} | {len(band) - right} |")
    w("")
    w("## Accuracy by layout")
    w("")
    w("| Layout | Set | Invoices | " + " | ".join(SCALAR_FIELDS) + " | All fields |")
    w("|---|---|---|" + "---|" * (len(SCALAR_FIELDS) + 1))
    for layout in (*LAYOUTS, *STRESS_LAYOUTS):
        rs = by_layout.get(layout, [])
        if rs:
            cells = " | ".join(pct(*acc(rs, (n,))) for n in SCALAR_FIELDS)
            w(f"| {layout} | {kind_of(layout)} | {len(rs)} | {cells} | {pct(*acc(rs))} |")
    w("")
    w("## Line items")
    w("")
    w(
        "A row counts only when description, quantity, unit price and amount all match. "
        'A table is "flagged" when any of its rows is under the threshold or the Review sheet carries a note about the table.'
    )
    w("")
    w(
        "| Layout | Set | Rows found exactly | Extra or wrong rows | Invoices with the whole table exact | Wrong tables passed as confident | Exact tables flagged anyway |"
    )
    w("|---|---|---|---|---|---|---|")

    def line_row(label: str, kind: str, rs: list[dict]) -> str:
        s = [r["score"] for r in rs]
        wrong_tables = [x for x in s if not x["table_exact"]]
        missed = sum(1 for x in wrong_tables if not x["table_flagged"])
        over = sum(1 for x in s if x["table_exact"] and x["table_flagged"])
        return (
            f"| {label} | {kind} | {frac(sum(x['rows_matched'] for x in s), sum(x['rows_truth'] for x in s))} | "
            f"{sum(x['rows_extra'] for x in s)} | {frac(sum(x['table_exact'] for x in s), len(s))} | "
            f"{missed} of {len(wrong_tables)} wrong | {over} |"
        )

    for layout in (*LAYOUTS, *STRESS_LAYOUTS):
        if by_layout.get(layout):
            w(line_row(layout, kind_of(layout), by_layout[layout]))
    w(line_row("**Standard, all**", "", standard))
    if stress:
        w(line_row("**Stress, all**", "", stress))
    w("")
    w("Line-item precision, recall and F1 (a row is one item; rows are matched as whole rows):")
    w("")
    w("| Set | Precision | Recall | F1 |")
    w("|---|---|---|---|")
    for label, rs in (("Standard", standard), ("Stress", stress)):
        matched = sum(r["score"]["rows_matched"] for r in rs)
        extra = sum(r["score"]["rows_extra"] for r in rs)
        truth = sum(r["score"]["rows_truth"] for r in rs)
        if not truth:
            continue
        prec, rec = matched / max(1, matched + extra), matched / truth
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        w(f"| {label} | {100 * prec:.1f}% | {100 * rec:.1f}% | {100 * f1:.1f}% |")
    w("")
    w("## Scanned PDFs")
    w("")
    detected = sum(1 for s in scans if s["needs_ocr"])
    w(
        f"{len(scans)} image-only PDFs were mixed in. **{frac(detected, len(scans))}** were detected and reported "
        "in the Review sheet as needing OCR. No fields were extracted from them and none were scored above. "
        "OCR itself is not part of this tool (see LIMITATIONS.md)."
    )
    w("")
    if stress:
        w("## The stress layouts")
        w("")
        w(
            "These three layouts were written to break the extractor, after the standard results came back clean and left nothing to measure the review sheet against. "
            "They were run once, for this report, and nothing in the extractor was changed afterwards. Low accuracy here is expected. "
            "The useful question is the last column of the headline table: when the extractor is wrong, does it know?"
        )
        w("")
        for layout in STRESS_LAYOUTS:
            w(f"- `{layout}`: {STRESS_NOTES[layout]}")
        w("")
        w("Wrong values passed as confident on the stress layouts, grouped:")
        w("")
        missed_table(stress)
        w("")
    w("## How the split works")
    w("")
    w(
        f"- Tuning layouts: {', '.join(f'`{x}`' for x in TUNING_LAYOUTS)}. The extractor's rules and confidence weights were adjusted while looking at failures on these."
    )
    w(
        f"- Held-out layouts: {', '.join(f'`{x}`' for x in HELD_OUT_LAYOUTS)}. They were written after the rules for the tuning layouts were done, and the extractor was run on them once. Nothing was changed afterwards."
    )
    w(
        f"- Stress layouts: {', '.join(f'`{x}`' for x in STRESS_LAYOUTS)}. Never tuned on, run once, reported as they came out."
    )
    w(
        "- Honest caveat: the same author wrote the generator and the extractor, so the held-out layouts are unseen by the code but not by its author. "
        "All PDFs here are clean, machine-made text. Real invoices from a new vendor can be harder than the standard layouts, and the stress results are the better guide to what a first run on unfamiliar documents looks like. See LIMITATIONS.md."
    )
    history = ROOT / "benchmark" / "history" / "round1_BENCHMARK.md"
    if history.exists():
        row = next(
            (
                ln
                for ln in history.read_text(encoding="utf-8").splitlines()
                if ln.startswith("| Held-out layouts |")
            ),
            None,
        )
        w("")
        w("### Round 1, kept for the record")
        w("")
        w(
            f"The first held-out set was {', '.join(f'`{x}`' for x in ROUND1_HELD_OUT)}, with the extractor tuned on five layouts only. "
            "That run found two bugs: a value printed in a larger font than its label was attached to the wrong text line "
            "(every due date on `services` was missed, and sent to review), and an `&` inside a customer name was dropped "
            "(one truncated name was passed as confident). Both were fixed, so those three layouts are no longer unseen "
            "and are counted as tuning layouts above. The untouched round 1 report is in "
            "[benchmark/history/round1_BENCHMARK.md](benchmark/history/round1_BENCHMARK.md). Its held-out row:"
        )
        if row:
            w("")
            w(
                "| Set | Header fields correct | Wrong values passed as confident | Wrong values caught by review |"
            )
            w("|---|---|---|---|")
            w(row.replace("Held-out layouts", "Round 1 held-out layouts"))
    w("")
    w("## Wrong values passed as confident on the standard layouts")
    w("")
    missed_table(standard)
    w("")
    w("## Reproduce")
    w("")
    w("```")
    w("python -m benchmark.run_benchmark")
    w("```")
    w("")
    return "\n".join(out)


def update_readme(report: str) -> None:
    """Copy the headline table into README.md so its numbers cannot drift from the report."""
    readme = ROOT / "README.md"
    start, end = "<!-- benchmark:start -->", "<!-- benchmark:end -->"
    text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    if start not in text or end not in text:
        return
    lines = (
        report.split("## Headline")[1]
        .split("## What the review sheet costs")[0]
        .strip()
        .splitlines()
    )
    block = "\n".join(ln for ln in lines if ln.startswith(("|", "**")) or not ln).strip()
    head, rest = text.split(start, 1)
    tail = rest.split(end, 1)[1]
    readme.write_text(f"{head}{start}\n{block}\n{end}{tail}", encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data", type=Path, default=ROOT / "benchmark" / "data")
    p.add_argument("--per-layout", type=int, default=40)
    p.add_argument("--scans", type=int, default=12)
    p.add_argument("--seed", type=int, default=20261120)
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument(
        "--tuning-only",
        action="store_true",
        help="development loop: tuning layouts, no report file",
    )
    p.add_argument(
        "--show-errors", type=int, default=0, help="print this many wrong or flagged fields"
    )
    args = p.parse_args()

    layouts = TUNING_LAYOUTS if args.tuning_only else (*LAYOUTS, *STRESS_LAYOUTS)
    if args.data.exists():
        shutil.rmtree(args.data)
    pdfs = generate(
        args.data, args.per_layout, args.seed, 0 if args.tuning_only else args.scans, layouts
    )

    rows, scans, pages = [], [], 0
    started = time.perf_counter()
    for pdf in pdfs:
        truth = json.loads(pdf.with_suffix(".json").read_text(encoding="utf-8"))
        result = extract_invoice(pdf)
        pages += result.page_count
        if truth["scanned"]:
            scans.append({"file": pdf.name, "needs_ocr": result.needs_ocr})
        else:
            rows.append(
                {
                    "file": pdf.name,
                    "layout": truth["layout"],
                    "score": (sc := score(truth, result, args.threshold)),
                    "status": status_of(result, args.threshold),
                    "exact": sc["table_exact"] and all(c["correct"] for c in sc["fields"].values()),
                    "result": result,
                }
            )
    elapsed = time.perf_counter() - started
    meta = {
        "seed": args.seed,
        "threshold": args.threshold,
        "pages": pages,
        "python": platform.python_version(),
        "pdfplumber": version("pdfplumber"),
        "seconds_per_pdf": elapsed / max(1, len(pdfs)),
    }
    report = build_report(rows, scans, meta)
    if args.tuning_only:
        print(report.split("## Scanned PDFs")[0])
    else:
        (ROOT / "BENCHMARK.md").write_text(report, encoding="utf-8")
        update_readme(report)
        print(report.split("## Accuracy by field")[0])
        print(f"BENCHMARK.md written ({len(rows)} invoices, {len(scans)} scans, {elapsed:.0f} s)")
    shown = 0
    for r in rows:
        for n, c in r["score"]["fields"].items():
            f = r["result"].get(n)
            if shown >= args.show_errors:
                break
            if not c["correct"]:
                print(
                    f"WRONG {r['file']} {n}: got {c['got']!r} want {c['want']!r} conf {f.confidence} {f.reasons}"
                )
                shown += 1
            elif c["flagged"]:
                print(
                    f"FLAGGED-OK {r['file']} {n}: {c['got']!r} conf {f.confidence} {f.method} {f.reasons}"
                )
                shown += 1
        if not r["score"]["table_exact"] and shown < args.show_errors:
            s = r["score"]
            print(
                f"TABLE {r['file']}: {s['rows_matched']}/{s['rows_truth']} extra {s['rows_extra']} {r['result'].line_items_reasons}"
            )
            shown += 1


if __name__ == "__main__":
    main()
