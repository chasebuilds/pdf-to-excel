from __future__ import annotations

import random
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen.canvas import Canvas

from benchmark.generate import Invoice, make_invoice, render


@pytest.fixture
def invoice_pdf(tmp_path: Path):
    """Render one synthetic invoice; ``tweak`` may change it before it is drawn."""

    def build(layout: str = "classic", seed: int = 1, tweak=None) -> tuple[Path, Invoice]:
        inv = make_invoice(layout, random.Random(f"test-{layout}-{seed}"))
        if tweak:
            tweak(inv)
        path = tmp_path / f"{layout}_{seed}.pdf"
        render(inv, path)
        return path, inv

    return build


@pytest.fixture
def text_pdf(tmp_path: Path):
    """A PDF from (x, y_from_top, text) triples, for layouts the generator does not make."""

    def build(
        rows: list[tuple[float, float, str]], name: str = "custom.pdf", size: float = 10
    ) -> Path:
        path = tmp_path / name
        c = Canvas(str(path), pagesize=LETTER, invariant=1)
        c.setFont("Helvetica", size)
        for x, y, text in rows:
            c.drawString(x, LETTER[1] - y, text)
        c.save()
        return path

    return build
