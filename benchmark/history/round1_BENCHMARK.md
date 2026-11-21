# Benchmark

Written by `benchmark/run_benchmark.py` from a real run. Do not edit by hand: rerun the script.

- Invoices scored: **320** text PDFs in 8 layouts, plus 12 scanned (image-only) PDFs
- Pages read: 377, line items in ground truth: 4346
- Generator seed: `20261120`, confidence threshold: `0.8`
- Local model fallback: **off** (all numbers are from deterministic code)
- Python 3.14.3, pdfplumber 0.11.11, 0.03 s per PDF on the test machine

## Headline

A field counts as correct only on an exact match with the ground truth (text compared without case or extra spaces, dates and amounts compared as values).

| Set | Header fields correct | Wrong values passed as confident | Wrong values caught by review |
|---|---|---|---|
| All layouts | 98.6% (2839/2880) | 0.0% (1/2880) | 97.6% (40/41) |
| Tuning layouts | 100.0% (1800/1800) | 0.0% (0/1800) | no wrong values |
| Held-out layouts | 96.2% (1039/1080) | 0.1% (1/1080) | 97.6% (40/41) |

**Confident-but-wrong rate, all layouts: 0.0% (1/2880) of header fields.** This is the number that matters most: a wrong value that was not sent to the Review sheet.

## What the review sheet costs

| Outcome | Fields | Share |
|---|---|---|
| Correct, passed as confident | 2788 | 96.8% |
| Correct, but sent to review anyway (extra checking for the client) | 51 | 1.8% |
| Wrong or missing, sent to review (caught) | 40 | 1.4% |
| Wrong, passed as confident (missed) | 1 | 0.0% |

## Accuracy by field

| Field | All | Tuning | Held out | Wrong and confident (all) |
|---|---|---|---|---|
| vendor | 100.0% (320/320) | 100.0% (200/200) | 100.0% (120/120) | 0 |
| invoice_number | 100.0% (320/320) | 100.0% (200/200) | 100.0% (120/120) | 0 |
| invoice_date | 100.0% (320/320) | 100.0% (200/200) | 100.0% (120/120) | 0 |
| due_date | 87.5% (280/320) | 100.0% (200/200) | 66.7% (80/120) | 0 |
| bill_to | 99.7% (319/320) | 100.0% (200/200) | 99.2% (119/120) | 1 |
| currency | 100.0% (320/320) | 100.0% (200/200) | 100.0% (120/120) | 0 |
| subtotal | 100.0% (320/320) | 100.0% (200/200) | 100.0% (120/120) | 0 |
| tax | 100.0% (320/320) | 100.0% (200/200) | 100.0% (120/120) | 0 |
| total | 100.0% (320/320) | 100.0% (200/200) | 100.0% (120/120) | 0 |

## Accuracy by layout

| Layout | Set | Invoices | vendor | invoice_number | invoice_date | due_date | bill_to | currency | subtotal | tax | total | All fields |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| classic | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| euro | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| minimal | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| ledger | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| boxed | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| letter | **held out** | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| services | **held out** | 40 | 100.0% | 100.0% | 100.0% | 0.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 88.9% |
| swiss | **held out** | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 97.5% | 100.0% | 100.0% | 100.0% | 100.0% | 99.7% |

## Line items

A row counts only when description, quantity, unit price and amount all match.

| Layout | Set | Rows found exactly | Extra or wrong rows | Invoices with the whole table exact | Wrong tables passed as confident |
|---|---|---|---|---|---|
| classic | tuning | 100.0% (271/271) | 0 | 100.0% (40/40) | 0 of 0 wrong |
| euro | tuning | 100.0% (280/280) | 0 | 100.0% (40/40) | 0 of 0 wrong |
| minimal | tuning | 100.0% (295/295) | 0 | 100.0% (40/40) | 0 of 0 wrong |
| ledger | tuning | 100.0% (1809/1809) | 0 | 100.0% (40/40) | 0 of 0 wrong |
| boxed | tuning | 100.0% (297/297) | 0 | 100.0% (40/40) | 0 of 0 wrong |
| letter | **held out** | 100.0% (242/242) | 0 | 100.0% (40/40) | 0 of 0 wrong |
| services | **held out** | 100.0% (245/245) | 0 | 100.0% (40/40) | 0 of 0 wrong |
| swiss | **held out** | 100.0% (907/907) | 0 | 100.0% (40/40) | 0 of 0 wrong |
| **All** |  | 100.0% (4346/4346) | 0 | 100.0% (320/320) | 0 of 0 wrong |

## Scanned PDFs

12 image-only PDFs were mixed in. **100.0% (12/12)** were detected and reported in the Review sheet as needing OCR. No fields were extracted from them and none were scored above. OCR itself is not part of this tool (see LIMITATIONS.md).

## How the split works

- Tuning layouts: `classic`, `euro`, `minimal`, `ledger`, `boxed`. The extractor's rules and confidence weights were adjusted while looking at failures on these.
- Held-out layouts: `letter`, `services`, `swiss`. The extractor was not run on these until the rules were frozen.
- Honest caveat: the same author wrote the generator and the extractor, so the held-out layouts are unseen by the code but not by its author. Real invoices from a new vendor can be harder than any of these. See LIMITATIONS.md.

## Every wrong value that was passed as confident

| File | Field | Extracted | Truth |
|---|---|---|---|
| swiss_018.pdf | bill_to | Oakline | Oakline & Sons |

## Reproduce

```
python -m benchmark.run_benchmark
```
