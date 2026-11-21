"""Tilted pages (phone scans, crooked OCR layers) are straightened before reading."""

from __future__ import annotations

import pytest

from benchmark.run_benchmark import norm
from benchmark.skew import SOURCE, tilt, truth
from pdf_to_excel import DEFAULT_THRESHOLD, extract_invoice
from pdf_to_excel.models import SCALAR_FIELDS
from pdf_to_excel.reader import SKEW_MAX_DEG, read_pdf


def assert_reads_like_the_straight_copy(path):
    r = extract_invoice(path)
    want = truth()
    for name in SCALAR_FIELDS:
        f = r.get(name)
        assert norm(name, f.value) == norm(name, want[name]), (name, f.value, want[name])
        assert f.confidence >= DEFAULT_THRESHOLD, (name, f.confidence, f.reasons)
    assert len(r.line_items) == len(want["line_items"])
    assert not r.line_items_reasons
    return r


@pytest.mark.parametrize("angle", [1.5, 3.0, 5.0, -3.0])
def test_tilted_copy_reads_like_the_straight_one(tmp_path, angle):
    path = tilt(SOURCE, tmp_path / "tilted.pdf", angle)
    assert read_pdf(path).pages[0].skew == pytest.approx(angle, abs=0.15)
    r = assert_reads_like_the_straight_copy(path)
    assert any("straightened" in n for n in r.file_notes)


def test_blurred_phone_scan_with_text_layer_reads(tmp_path):
    path = tilt(SOURCE, tmp_path / "phone.pdf", 3.0, blur_dpi=60)
    assert not read_pdf(path).scanned_pages
    assert_reads_like_the_straight_copy(path)


def test_without_deskew_a_tilted_copy_is_sent_to_review(tmp_path):
    path = tilt(SOURCE, tmp_path / "tilted.pdf", 3.0)
    r = extract_invoice(path, deskew=False)
    assert all(r.get(n).confidence < DEFAULT_THRESHOLD for n in SCALAR_FIELDS[:2])


def test_straight_page_is_left_alone():
    doc = read_pdf(SOURCE)
    assert [p.skew for p in doc.pages] == [0.0]
    assert not any("straightened" in n for n in extract_invoice(SOURCE).file_notes)


def test_steep_angle_is_not_treated_as_a_crooked_scan(tmp_path):
    path = tilt(SOURCE, tmp_path / "steep.pdf", SKEW_MAX_DEG + 10)
    assert read_pdf(path).pages[0].skew == 0.0


def test_scattered_letters_guard_still_holds_without_deskew(tmp_path):
    # the drill's guard: a tilted page read letter by letter must not give a confident vendor
    path = tilt(SOURCE, tmp_path / "tilted.pdf", 1.5)
    f = extract_invoice(path, deskew=False).get("vendor")
    tokens = str(f.value or "").split()
    scattered = len(tokens) > 2 and sum(len(t) == 1 for t in tokens) * 2 > len(tokens)
    assert not (scattered and f.confidence >= DEFAULT_THRESHOLD), (f.value, f.confidence)
