"""Command line: a folder of invoice PDFs in, one workbook out."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .extract import check_batch, extract_invoice
from .llm import apply_llm_fallback
from .models import DEFAULT_THRESHOLD
from .workbook import status_of, write_workbook


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pdf-to-excel",
        description="Read invoice PDFs and write one Excel workbook, "
        "with confidence scores and a review sheet.",
    )
    p.add_argument("input", type=Path, help="a folder of PDFs, or one PDF")
    p.add_argument(
        "-o", "--output", type=Path, default=Path("invoices.xlsx"), help="workbook to write"
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"fields below this confidence go to the Review sheet (default {DEFAULT_THRESHOLD})",
    )
    p.add_argument(
        "--json",
        type=Path,
        metavar="PATH",
        help="also write the raw results as JSON; '-' prints them to stdout "
        "and moves the summary line to stderr",
    )
    p.add_argument(
        "--audit-sample",
        type=int,
        default=None,
        help="how many OK invoices to add to the Review sheet as a random spot check "
        "(default: 5 percent, at least 1; 0 turns it off)",
    )
    p.add_argument(
        "--llm-model",
        help="optional: ask this local Ollama model about low-confidence fields (off by default)",
    )
    p.add_argument("--version", action="version", version=f"pdf-to-excel {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.input.exists():
        print(f"error: {args.input} does not exist", file=sys.stderr)
        return 2
    files = [args.input] if args.input.is_file() else sorted(args.input.glob("*.pdf"))
    if not files:
        print(f"error: no PDF files in {args.input}", file=sys.stderr)
        return 2
    results = []
    for path in files:
        result = extract_invoice(path)
        if args.llm_model:
            apply_llm_fallback(result, path, args.llm_model, args.threshold)
        results.append(result)
    check_batch(results)
    out = write_workbook(results, args.output, args.threshold, audit_sample=args.audit_sample)
    statuses = [status_of(r, args.threshold) for r in results]
    to_stdout = str(args.json) == "-"
    if args.json:
        payload = [{**r.as_dict(), "status": s} for r, s in zip(results, statuses, strict=True)]
        text = json.dumps(payload, indent=2)
        if to_stdout:
            print(text)
        else:
            args.json.write_text(text, encoding="utf-8")
    other = sum(1 for s in statuses if s not in ("OK", "REVIEW", "NEEDS OCR", "ERROR"))
    print(
        f"{len(results)} files -> {out}  |  OK {statuses.count('OK')}, "
        f"REVIEW {statuses.count('REVIEW')}, NEEDS OCR {statuses.count('NEEDS OCR')}, "
        f"ERROR {statuses.count('ERROR')}, other not read {other}",
        file=sys.stderr if to_stdout else sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
