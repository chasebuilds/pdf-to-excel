"""Write the handful of PDFs in samples/invoices. Seeded, so they are the same every time.

python -m benchmark.make_samples
"""

from __future__ import annotations

import random
from decimal import Decimal
from pathlib import Path

from benchmark.generate import make_invoice, make_scan, render

OUT = Path(__file__).resolve().parent.parent / "samples" / "invoices"
# (file name, layout, seed)
PICKS = [
    ("01_classic_us", "classic", 3),
    ("02_euro_comma_decimals", "euro", 2),
    ("03_minimal_stacked_labels", "minimal", 1),
    ("04_ledger_multi_page", "ledger", 5),
    ("05_services_wrapped_lines", "services", 2),
    ("06_statement_from_bill_to", "statement", 1),
    ("07_unfamiliar_wording", "stress_wording", 1),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.pdf"):
        old.unlink()
    for name, layout, seed in PICKS:
        render(make_invoice(layout, random.Random(f"sample-{layout}-{seed}")), OUT / f"{name}.pdf")

    # an invoice whose printed total does not add up: the arithmetic check should catch it
    wrong = make_invoice("boxed", random.Random("sample-wrong-total"))
    wrong.total_override = wrong.total + Decimal("100.00")
    render(wrong, OUT / "08_total_does_not_add_up.pdf")

    # a scan: image only, no text layer
    source = OUT / "_scan_source.pdf"
    render(make_invoice("classic", random.Random("sample-scan")), source)
    make_scan(source, OUT / "09_scanned_image_only.pdf")
    source.unlink()
    print(f"{len(list(OUT.glob('*.pdf')))} sample PDFs in {OUT}")


if __name__ == "__main__":
    main()
