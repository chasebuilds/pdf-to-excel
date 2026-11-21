"""Tilted and blurred copies of a sample invoice, read with and without deskew.

    python -m benchmark.skew            # writes samples/skewed/*.pdf, prints the table

The source is samples/invoices/01_classic_us.pdf. Each copy keeps its text layer,
the way a phone scanner app or a scanner's OCR step saves a PDF. A pure image with
no text layer is a different case: that still comes back as NEEDS OCR.
"""

from __future__ import annotations

import random
from pathlib import Path

from benchmark.generate import make_invoice
from benchmark.make_samples import OUT as SAMPLES
from benchmark.run_benchmark import norm
from pdf_to_excel import DEFAULT_THRESHOLD, extract_invoice
from pdf_to_excel.models import SCALAR_FIELDS

SOURCE = SAMPLES / "01_classic_us.pdf"
OUT = SAMPLES.parent / "skewed"
ANGLES = (1.5, 3.0, 5.0)
BLUR_ANGLE = 3.0


def truth() -> dict:
    # same seed make_samples uses for 01_classic_us
    return make_invoice("classic", random.Random("sample-classic-3")).truth()


def tilt(src: Path, out: Path, angle: float, blur_dpi: int | None = None) -> Path:
    """Copy page 1 of ``src`` turned ``angle`` degrees counter-clockwise.

    With ``blur_dpi`` a soft low-resolution picture of the tilted page is laid over
    the text, like a phone photo with its OCR layer underneath.
    """
    import pymupdf

    with pymupdf.open(src) as s, pymupdf.open() as dst:
        page = dst.new_page(width=s[0].rect.width, height=s[0].rect.height)
        page.show_pdf_page(page.rect, s, 0, rotate=angle)
        if blur_dpi:
            pix = page.get_pixmap(dpi=blur_dpi, colorspace=pymupdf.csGRAY)
            page.insert_image(page.rect, pixmap=pix, overlay=True)
        dst.save(out, deflate=True, no_new_id=True)
    return out


def make_fixtures(out: Path = OUT, src: Path = SOURCE) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    made = [
        tilt(src, out / f"tilt_{str(a).replace('.', '_').removesuffix('_0')}deg.pdf", a)
        for a in ANGLES
    ]
    made.append(tilt(src, out / "phone_scan_blurred_3deg.pdf", BLUR_ANGLE, blur_dpi=60))
    return made


def read(path: Path, deskew: bool) -> dict:
    r = extract_invoice(path, deskew=deskew)
    want = truth()
    fields = [r.get(n) for n in SCALAR_FIELDS]
    right = [
        norm(n, f.value) == norm(n, want[n]) for n, f in zip(SCALAR_FIELDS, fields, strict=True)
    ]
    return {
        "correct": sum(right),
        "confident_correct": sum(
            ok and f.confidence >= DEFAULT_THRESHOLD for ok, f in zip(right, fields, strict=True)
        ),
        "confident_wrong": sum(
            not ok and f.value is not None and f.confidence >= DEFAULT_THRESHOLD
            for ok, f in zip(right, fields, strict=True)
        ),
        "mean_conf": sum(f.confidence for f in fields) / len(fields),
        "rows": len(r.line_items),
        "status": r.problem
        or ("OK" if all(f.confidence >= DEFAULT_THRESHOLD for f in fields) else "REVIEW"),
    }


def main() -> None:
    files = [SOURCE, *make_fixtures()]
    n = len(SCALAR_FIELDS)
    rows_want = len(truth()["line_items"])
    print(
        f"| File | Deskew | Fields right | Confident and right | Confident but wrong | Mean confidence | Line items ({rows_want}) | Status |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for f in files:
        for deskew in (False, True):
            s = read(f, deskew)
            print(
                f"| {f.name} | {'on' if deskew else 'off'} | {s['correct']}/{n} | {s['confident_correct']}/{n} "
                f"| {s['confident_wrong']} | {s['mean_conf']:.2f} | {s['rows']} | {s['status']} |"
            )


if __name__ == "__main__":
    main()
