window.DEMO = {
  repo: "pdf-to-excel",
  title: "pdf-to-excel",
  tagline: "Drop in invoice PDFs. Get one workbook with the header fields, the line items and a review list of anything that did not check out, such as totals that do not add up.",
  pkg: "pdf_to_excel",
  packages: ["openpyxl", "pdfminer.six", "pillow"],
  pip_nodeps: ["pdfplumber"],
  inputs: [
    { id: "files", label: "Invoice PDFs (pick several)", accept: ".pdf", multiple: true,
      sample: ["01_classic_us.pdf", "02_euro_comma_decimals.pdf", "03_minimal_stacked_labels.pdf", "04_ledger_multi_page.pdf",
        "05_services_wrapped_lines.pdf", "06_statement_from_bill_to.pdf", "07_unfamiliar_wording.pdf",
        "08_total_does_not_add_up.pdf", "09_scanned_image_only.pdf"].map((f) => `samples/${f}`) },
  ],
  options: [
    { id: "threshold", label: "Confidence needed to pass", type: "text", value: "" , placeholder: "default" },
  ],
  sampleNote: 'The sample is nine synthetic invoices from invented companies, in different layouts: euro decimals, stacked labels, a multi-page ledger, wrapped lines, one whose total does not add up and one scanned image with no text layer.<br>pdfplumber runs here without pypdfium2 (it has no WebAssembly build and is only used to render page images, which this tool does not need). Scanned PDFs are marked NEEDS OCR, the same as on a desktop. The optional AI fallback is off in the browser.',
  python: `
from pathlib import Path
from pdf_to_excel.cli import main as cli_main

def run(paths, opts):
    out = "/work/invoices.xlsx"
    argv = ["/work/in/files", "-o", out]
    if opts.get("threshold", "").strip():
        argv += ["--threshold", opts["threshold"].strip()]
    code, log = run_cli(cli_main, argv)
    if not Path(out).exists():
        return {"ok": False, "log": log, "sheets": [], "download": None}
    return {"ok": True, "log": log, "sheets": sheets_from_xlsx(out), "download": out}
`,
};
