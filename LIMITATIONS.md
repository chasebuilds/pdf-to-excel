# Limitations

What this tool does not do, and where it is known to be weak. Numbers quoted here come from [BENCHMARK.md](BENCHMARK.md).

## About the evidence

- **Synthetic data only.** Every invoice in the benchmark was drawn by the generator in this repo. No real client invoices were used. The PDFs are clean, machine-made text: no skew, no stamps, no handwriting, no fonts with broken text encoding.
- **One author.** The same author wrote the generator and the extractor. The two held-out layouts were unseen by the code, not by its author. The 100% on the standard layouts says the method works on tidy invoices with ordinary wording. It does not say a new vendor's real invoices will score 100%.
- **The stress layouts are the more honest guide** to a first run on unfamiliar documents: about 73% of header fields right, and about 95% of the wrong ones sent to review.
- **Confidence is not a probability.** The scores are hand-set weights for label strength, value position, pattern and arithmetic. The 0.80 threshold was chosen on the tuning layouts. A field at 0.90 is not "90% likely correct". BENCHMARK.md has a table of how often each confidence band was right, on synthetic data. Commercial document services suggest 0.90 or higher for financial fields: use `--threshold 0.9` when a wrong value is costly, and check the audit sample on the Review sheet for your own documents.

## Not supported

- **OCR.** Scanned, image-only PDFs are detected (a page with almost no text and an image covering most of it) and listed in the Review sheet as needing OCR. They are not read, and there is no `--ocr` option. If a scan already has a text layer from someone else's OCR, that layer is read as it is, mistakes included.
- **Subtly wrong text layers.** A text layer that is obvious garbage ("(cid:12)" codes, or mostly symbols) is caught and reported as `BAD TEXT LAYER`. A layer that looks like text but has wrong characters (an OCR pass that read 8 as 3) is not caught, except where the arithmetic checks happen to expose it.
- **E-invoice attachments.** Factur-X and ZUGFeRD PDFs carry the invoice as embedded XML. This tool does not read the attachment. It reads the visible page like any other PDF.
- **Fillable form PDFs.** Values typed into form fields are not read.
- **Languages other than English.** The label list is English ("Invoice No", "Due Date", "Subtotal", "VAT"...). A few common European tax words are included, nothing more.
- **More than one invoice in a PDF.** Each file is treated as one invoice.
- **Credit notes, statements, receipts, purchase orders.** Negative amounts parse, but none of these document types was tested.
- **Password-protected or damaged PDFs.** They get an `ENCRYPTED` or `ERROR` row in the workbook and the batch continues.
- **Rotated pages.** They are noted in the Review sheet and read anyway, without being turned upright. Expect wrong or blank fields.
- **Right-to-left text, handwriting, text set in two newspaper-style columns.**
- **Currency conversion.** The Summary sheet gives one total per currency. Nothing is converted.
- **Where on the page a value came from.** The Review sheet gives file and page, not coordinates or a marked-up image.

## Known weak spots

- **Seller name.** Invoices rarely label the seller, so this field is found by layout: the largest text near the top, the first line, a company-style ending such as "Ltd" or "LLC". It fails when:
  - the seller is named only in a footer or inside a logo image, and an unlabelled customer block sits at the top of the page. The customer is then taken for the seller, sometimes with high confidence. This is the one confident-and-wrong pattern the stress test found: 14 of 40 invoices in the `stress_wording` layout. Sample `07_unfamiliar_wording.pdf` shows it.
  - the name is set in letter-spaced capitals ("A C M E"). It is returned blank and flagged.
- **Unfamiliar label wording.** "Raised" for the invoice date, "Settle by" for the due date, "To pay" for the total are not in the label list. Those fields come back blank and flagged. Adding wording for a specific client is a small change to `labels.py`.
- **Day and month order.** `03/04/2026` is 4 March in one country and 3 April in another. The tool looks for evidence: another date on the page with a day above 12, the gap between invoice date and due date, comma decimals. Without evidence it assumes month first and flags the field. With two-digit years it assumes 20xx.
- **Bare `$`.** A dollar sign with no currency code anywhere is reported as USD at confidence 0.85, which passes the default threshold. A Canadian or Australian invoice that never says "CAD" or "AUD" would be labelled USD without a flag. Raise `--threshold` above 0.85 if that matters for a batch.
- **Weak labels.** A bare "Reference" or "Ref" is accepted as the invoice number only when nothing better is on the page, and usually flagged.

## Line items

- A table is found by its header row (words like Description, Qty, Unit Price, Amount) or, failing that, by rows whose last three numbers satisfy quantity x price = amount. A table with neither is not found, and the Review sheet says so.
- Only description, quantity, unit price and amount are extracted. Other columns (SKU, discount, per-line tax, unit of measure) are skipped.
- Per-line discounts or per-line tax mean quantity x price no longer equals the amount. The values are still read, but every such row is flagged. In the `stress_table` layout all 40 tables were read exactly and all 40 were flagged.
- Wrapped descriptions are joined to the row above. This assumes the numbers sit on the first line of a wrapped row. Tables that centre the numbers vertically against a tall description will attach text to the wrong row.
- Sub-totals inside a table, section headings between rows, and merged cells were not tested.

## Totals

- The check is subtotal + tax (+ shipping, freight, delivery or handling, minus discount, when labelled) = total. Any other charge makes the check fail and flags all three amounts, even when they were read correctly.
- Only one tax amount is taken. An invoice with two tax lines (for example GST and PST) will fail the check and be flagged.
- When a total label is missing but subtotal and tax are present, the total is derived and flagged as derived. Nothing is filled in silently.
- When the arithmetic fails, the tool cannot tell whether it misread a number or the invoice itself is wrong. It reports the numbers and leaves the call to a person.
- The duplicate check compares vendor name and invoice number exactly (ignoring case) within one run. It does not know about invoices from earlier runs, and it misses duplicates where the vendor name was read differently.
- The date check uses the clock of the machine running the tool.

## The optional local model

- Off by default. Not used in the tests or in any benchmark number.
- Tried once by hand against a local Ollama model on one sample invoice: it filled five blank fields, four correctly and one wrongly (it gave the seller's footer line as the customer). Its accuracy has not been measured, which is why its answers stay in the Review sheet at confidence 0.5.
- A suggested value must be printed on the page to be accepted. That stops invented values. It does not stop the model from picking a real value from the wrong place: the wrong customer above was printed on the page, so it passed the rule.
- It only talks to `localhost`.

## Scale

- About 0.03 seconds per PDF on clean synthetic files on the test machine. Large or image-heavy real files will be slower. Batches are processed one file at a time in a single process.
