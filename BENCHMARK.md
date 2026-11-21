# Benchmark

Written by `benchmark/run_benchmark.py` from a real run. Do not edit by hand: rerun the script.

- Invoices scored: **520** text PDFs (400 in 10 standard layouts, 120 in 3 stress layouts), plus 12 scanned (image-only) PDFs
- Pages read: 576, line items in ground truth: 5748
- Generator seed: `20261120`, confidence threshold: `0.8`
- Local model fallback: **off** (all numbers are from deterministic code)
- Python 3.14.3, pdfplumber 0.11.11, 0.02 s per PDF on the test machine
- All data is synthetic. See the caveats under "How the split works" before quoting these numbers.

## Headline

A field counts as correct only on an exact match with the ground truth (text compared without case or extra spaces, dates and amounts compared as values). A missing value counts as wrong. "Passed as confident" means the confidence was at or above the threshold, so the value did not go to the Review sheet.

| Set | Invoices | Header fields correct | Wrong values passed as confident | Wrong values caught by review |
|---|---|---|---|---|
| Standard layouts (10) | 400 | 100.0% (3600/3600) | 0.0% (0/3600) | no wrong values |
| of which tuning layouts | 320 | 100.0% (2880/2880) | 0.0% (0/2880) | no wrong values |
| of which held-out layouts | 80 | 100.0% (720/720) | 0.0% (0/720) | no wrong values |
| Stress layouts (built to be hard, never tuned on) | 120 | 72.7% (785/1080) | 1.3% (14/1080) | 95.3% (281/295) |

**Confident-but-wrong rate: 0.0% (0/3600) of header fields on the standard layouts, 1.3% (14/1080) on the stress layouts.** This is the number that matters most: a wrong value that was not sent to the Review sheet.

## What the review sheet costs

Standard layouts:

| Outcome | Fields | Share |
|---|---|---|
| Correct, passed as confident | 3589 | 99.7% |
| Correct, but sent to review anyway (extra checking for the client) | 11 | 0.3% |
| Wrong or missing, sent to review (caught) | 0 | 0.0% |
| Wrong, passed as confident (missed) | 0 | 0.0% |

Stress layouts:

| Outcome | Fields | Share |
|---|---|---|
| Correct, passed as confident | 738 | 68.3% |
| Correct, but sent to review anyway (extra checking for the client) | 47 | 4.4% |
| Wrong or missing, sent to review (caught) | 281 | 26.0% |
| Wrong, passed as confident (missed) | 14 | 1.3% |

## Accuracy by field

| Field | Standard | Tuning | Held out | Stress | Wrong and confident (standard) | Wrong and confident (stress) |
|---|---|---|---|---|---|---|
| vendor | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 33.3% (40/120) | 0 | 14 |
| invoice_number | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 100.0% (120/120) | 0 | 0 |
| invoice_date | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 60.8% (73/120) | 0 | 0 |
| due_date | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 60.0% (72/120) | 0 | 0 |
| bill_to | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 66.7% (80/120) | 0 | 0 |
| currency | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 100.0% (120/120) | 0 | 0 |
| subtotal | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 66.7% (80/120) | 0 | 0 |
| tax | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 100.0% (120/120) | 0 | 0 |
| total | 100.0% (400/400) | 100.0% (320/320) | 100.0% (80/80) | 66.7% (80/120) | 0 | 0 |

## Precision and recall by field

Precision: of the values the tool wrote down, the share that were right. Recall: of the values printed on the invoices, the share the tool got right. A blank lowers recall but not precision, which is the trade this tool makes on purpose: it leaves a field empty and flags it before it guesses.

| Field | Precision, standard | Recall, standard | Precision, stress | Recall, stress |
|---|---|---|---|---|
| vendor | 100.0% (400/400) | 100.0% (400/400) | 50.0% (40/80) | 33.3% (40/120) |
| invoice_number | 100.0% (400/400) | 100.0% (400/400) | 100.0% (120/120) | 100.0% (120/120) |
| invoice_date | 100.0% (400/400) | 100.0% (400/400) | 91.2% (73/80) | 60.8% (73/120) |
| due_date | 100.0% (400/400) | 100.0% (400/400) | 90.0% (72/80) | 60.0% (72/120) |
| bill_to | 100.0% (400/400) | 100.0% (400/400) | 100.0% (80/80) | 66.7% (80/120) |
| currency | 100.0% (400/400) | 100.0% (400/400) | 100.0% (120/120) | 100.0% (120/120) |
| subtotal | 100.0% (400/400) | 100.0% (400/400) | 100.0% (80/80) | 66.7% (80/120) |
| tax | 100.0% (400/400) | 100.0% (400/400) | 100.0% (120/120) | 100.0% (120/120) |
| total | 100.0% (400/400) | 100.0% (400/400) | 100.0% (80/80) | 66.7% (80/120) |

## Whole documents

An invoice counts here only when all nine header fields and every line item are exactly right.

| Set | Invoices exactly right | of those, passed with status OK | Invoices with any error passed with status OK |
|---|---|---|---|
| Standard layouts (10) | 100.0% (400/400) | 394 | 0 of 0 |
| of which tuning layouts | 100.0% (320/320) | 314 | 0 of 0 |
| of which held-out layouts | 100.0% (80/80) | 80 | 0 of 0 |
| Stress layouts (built to be hard, never tuned on) | 29.2% (35/120) | 31 | 0 of 85 |

## What a confidence score means here

Confidence is a ranking score built from hand-set weights, not a probability. This table is the evidence for what a band is worth: every header field from every invoice in this run (standard and stress together), grouped by its score.

| Confidence band | Fields | Right | Share right | Wrong |
|---|---|---|---|---|
| 0.00 to 0.50 (review) | 240 | 0 | 0.0% | 240 |
| 0.50 to 0.80 (review) | 99 | 58 | 58.6% | 41 |
| 0.80 to 0.90 | 592 | 592 | 100.0% | 0 |
| 0.90 to 0.95 | 1125 | 1111 | 98.8% | 14 |
| 0.95 to 1.00 | 2624 | 2624 | 100.0% | 0 |

## Accuracy by layout

| Layout | Set | Invoices | vendor | invoice_number | invoice_date | due_date | bill_to | currency | subtotal | tax | total | All fields |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| classic | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| euro | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| minimal | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| ledger | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| boxed | tuning | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| letter | tuning (held out in round 1) | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| services | tuning (held out in round 1) | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| swiss | tuning (held out in round 1) | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| retail | **held out** | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| statement | **held out** | 40 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% |
| stress_wording | stress | 40 | 0.0% | 100.0% | 0.0% | 0.0% | 0.0% | 100.0% | 0.0% | 100.0% | 0.0% | 33.3% |
| stress_ambiguous | stress | 40 | 100.0% | 100.0% | 90.0% | 87.5% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 97.5% |
| stress_table | stress | 40 | 0.0% | 100.0% | 92.5% | 92.5% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 87.2% |

## Line items

A row counts only when description, quantity, unit price and amount all match. A table is "flagged" when any of its rows is under the threshold or the Review sheet carries a note about the table.

| Layout | Set | Rows found exactly | Extra or wrong rows | Invoices with the whole table exact | Wrong tables passed as confident | Exact tables flagged anyway |
|---|---|---|---|---|---|---|
| classic | tuning | 100.0% (271/271) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| euro | tuning | 100.0% (280/280) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| minimal | tuning | 100.0% (295/295) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| ledger | tuning | 100.0% (1809/1809) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| boxed | tuning | 100.0% (297/297) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| letter | tuning (held out in round 1) | 100.0% (242/242) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| services | tuning (held out in round 1) | 100.0% (245/245) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| swiss | tuning (held out in round 1) | 100.0% (907/907) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| retail | **held out** | 100.0% (289/289) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| statement | **held out** | 100.0% (248/248) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| stress_wording | stress | 100.0% (288/288) | 0 | 100.0% (40/40) | 0 of 0 wrong | 40 |
| stress_ambiguous | stress | 100.0% (282/282) | 0 | 100.0% (40/40) | 0 of 0 wrong | 0 |
| stress_table | stress | 100.0% (295/295) | 0 | 100.0% (40/40) | 0 of 0 wrong | 40 |
| **Standard, all** |  | 100.0% (4883/4883) | 0 | 100.0% (400/400) | 0 of 0 wrong | 0 |
| **Stress, all** |  | 100.0% (865/865) | 0 | 100.0% (120/120) | 0 of 0 wrong | 80 |

Line-item precision, recall and F1 (a row is one item; rows are matched as whole rows):

| Set | Precision | Recall | F1 |
|---|---|---|---|
| Standard | 100.0% | 100.0% | 100.0% |
| Stress | 100.0% | 100.0% | 100.0% |

## Scanned PDFs

12 image-only PDFs were mixed in. **100.0% (12/12)** were detected and reported in the Review sheet as needing OCR. No fields were extracted from them and none were scored above. OCR itself is not part of this tool (see LIMITATIONS.md).

## The stress layouts

These three layouts were written to break the extractor, after the standard results came back clean and left nothing to measure the review sheet against. They were run once, for this report, and nothing in the extractor was changed afterwards. Low accuracy here is expected. The useful question is the last column of the headline table: when the extractor is wrong, does it know?

- `stress_wording`: No familiar wording: `Our Ref`, `Raised`, `Settle by`, a `Particulars / Nos / @ / Value` table, `Goods` and `To pay` for the totals. The customer block has no label and the seller is named only in the footer.
- `stress_ambiguous`: Day-first slash dates with dot decimals (nothing on the page says which order), a quote number next to the invoice number, a ship-to block before the bill-to block, a deposit and balance after the total, and `$` that means AUD.
- `stress_table`: Seller name in letter-spaced capitals, two-digit years, and a discount column, so quantity x price does not equal the amount on discounted rows.

Wrong values passed as confident on the stress layouts, grouped:

| Layout | Field | Count | Example file | Extracted | Truth |
|---|---|---|---|---|---|
| stress_wording | vendor | 14 | stress_wording_001.pdf | Vantage Trading GmbH | Ironwood Analytics Ltd |

## How the split works

- Tuning layouts: `classic`, `euro`, `minimal`, `ledger`, `boxed`, `letter`, `services`, `swiss`. The extractor's rules and confidence weights were adjusted while looking at failures on these.
- Held-out layouts: `retail`, `statement`. They were written after the rules for the tuning layouts were done, and the extractor was run on them once. Nothing was changed afterwards.
- Stress layouts: `stress_wording`, `stress_ambiguous`, `stress_table`. Never tuned on, run once, reported as they came out.
- Honest caveat: the same author wrote the generator and the extractor, so the held-out layouts are unseen by the code but not by its author. All PDFs here are clean, machine-made text. Real invoices from a new vendor can be harder than the standard layouts, and the stress results are the better guide to what a first run on unfamiliar documents looks like. See LIMITATIONS.md.

### Round 1, kept for the record

The first held-out set was `letter`, `services`, `swiss`, with the extractor tuned on five layouts only. That run found two bugs: a value printed in a larger font than its label was attached to the wrong text line (every due date on `services` was missed, and sent to review), and an `&` inside a customer name was dropped (one truncated name was passed as confident). Both were fixed, so those three layouts are no longer unseen and are counted as tuning layouts above. The untouched round 1 report is in [benchmark/history/round1_BENCHMARK.md](benchmark/history/round1_BENCHMARK.md). Its held-out row:

| Set | Header fields correct | Wrong values passed as confident | Wrong values caught by review |
|---|---|---|---|
| Round 1 held-out layouts | 96.2% (1039/1080) | 0.1% (1/1080) | 97.6% (40/41) |

## Wrong values passed as confident on the standard layouts

None in this run.

## Reproduce

```
python -m benchmark.run_benchmark
```
