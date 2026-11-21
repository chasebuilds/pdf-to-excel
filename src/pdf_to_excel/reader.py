"""Read a PDF into positioned words, lines and cells. No field logic here."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pdfplumber
from pdfplumber.utils import extract_words

# Below this many characters a page with a large image is treated as a scan.
SCAN_MAX_CHARS = 20
SCAN_MIN_IMAGE_COVERAGE = 0.5
# Real invoices are mostly letters and digits. Far below this the text layer is garbage.
BAD_TEXT_MIN_ALNUM = 0.5
# A phone scan or a scanner's OCR layer is often a degree or a few off. Below MIN the
# line grouping copes; above MAX it is not a crooked page but a deliberate layout.
SKEW_MIN_DEG = 0.3
SKEW_MAX_DEG = 15.0

_GLUED = re.compile(r"^([A-Za-z#.]{2,}:)(\S+)$")


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    page: int


@dataclass
class Cell:
    """Words on one line with no wide gap between them."""

    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def x1(self) -> float:
        return self.words[-1].x1

    @property
    def mid(self) -> float:
        return (self.x0 + self.x1) / 2


def split_cells(words: list[Word]) -> list[Cell]:
    cells: list[Cell] = []
    for w in words:
        # a normal space is about 0.28 em, a column gap is far wider
        if cells and w.x0 - cells[-1].x1 <= 0.9 * max(w.size, 6.0):
            cells[-1].words.append(w)
        else:
            cells.append(Cell([w]))
    return cells


@dataclass
class Line:
    words: list[Word]
    page: int
    index: int = 0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def size(self) -> float:
        return max(w.size for w in self.words)

    @cached_property
    def cells(self) -> list[Cell]:
        return split_cells(self.words)


@dataclass
class Page:
    number: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    char_count: int = 0
    image_coverage: float = 0.0
    rotation: int = 0
    skew: float = 0.0  # degrees the text was tilted and straightened back

    @property
    def scanned(self) -> bool:
        return self.char_count < SCAN_MAX_CHARS and self.image_coverage >= SCAN_MIN_IMAGE_COVERAGE

    @property
    def bad_text(self) -> bool:
        """A text layer that is present but is not real text.

        Fonts without a usable character map come out as "(cid:12)" codes or as
        runs of symbols. Extracting fields from that would produce nonsense, so
        the page is reported instead.
        """
        if self.char_count < SCAN_MAX_CHARS:
            return False
        text = "".join(w.text for ln in self.lines for w in ln.words)
        if text.count("(cid:") >= 3:
            return True
        return sum(ch.isalnum() for ch in text) / len(text) < BAD_TEXT_MIN_ALNUM


@dataclass
class Document:
    path: Path
    pages: list[Page]

    @property
    def lines(self) -> list[Line]:
        return [ln for p in self.pages for ln in p.lines]

    @property
    def scanned_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.scanned]

    @property
    def has_text(self) -> bool:
        return any(p.lines for p in self.pages)

    @property
    def bad_text_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.bad_text]

    @property
    def rotated_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.rotation % 360]

    @property
    def skewed_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.skew]


def _unglue(w: Word) -> list[Word]:
    """Split "No:12345" into label and value so the label matcher sees both."""
    m = _GLUED.match(w.text)
    if not m:
        return [w]
    cut = w.x0 + (w.x1 - w.x0) * len(m[1]) / len(w.text)
    return [
        Word(m[1], w.x0, cut, w.top, w.bottom, w.size, w.page),
        Word(m[2], cut, w.x1, w.top, w.bottom, w.size, w.page),
    ]


def _group_lines(words: list[Word], page: int) -> list[Line]:
    # Words on one printed line share a baseline even when their font sizes differ,
    # so group on the bottom edge. Grouping on the vertical middle pulled a 10 pt value
    # onto the neighbouring 9 pt address line.
    lines: list[list[Word]] = []
    base: list[float] = []
    for w in sorted(words, key=lambda w: (w.bottom, w.x0)):
        if lines and abs(w.bottom - base[-1]) <= 2.0:
            lines[-1].append(w)
        else:
            lines.append([w])
            base.append(w.bottom)
    return [Line(sorted(ws, key=lambda w: w.x0), page) for ws in lines]


def estimate_skew(chars: list[dict]) -> float:
    """Angle of the text baselines in degrees, counter-clockwise positive.

    Taken from the step between neighbouring glyph origins inside a word, so it works
    both for text drawn rotated and for an OCR layer that places upright glyphs along a
    tilted line. The median ignores superscripts, kerning and the odd stray glyph.
    """
    slopes = []
    for a, b in zip(chars, chars[1:], strict=False):
        if not (a.get("matrix") and b.get("matrix")) or a["size"] != b["size"]:
            continue
        dx, dy = b["matrix"][4] - a["matrix"][4], b["matrix"][5] - a["matrix"][5]
        if 0 < dx < 1.5 * a["size"] and abs(dy) < 0.5 * dx:
            slopes.append(math.degrees(math.atan2(dy, dx)))
    if len(slopes) < 10:
        return 0.0
    slopes.sort()
    return slopes[len(slopes) // 2]


def straighten(chars: list[dict], angle: float, width: float, height: float) -> list[dict]:
    """Rotate glyph boxes back by ``angle`` about the page centre."""
    t = math.radians(angle)
    cos, sin = math.cos(t), math.sin(t)
    cx, cy = width / 2, height / 2
    den = cos * cos - sin * sin
    out = []
    for c in chars:
        # a w x h box turned by t has a bounding box of (w cos + h sin) x (w sin + h cos)
        bw, bh = c["x1"] - c["x0"], c["bottom"] - c["top"]
        w = max((bw * cos - bh * abs(sin)) / den, 0.1)
        h = max((bh * cos - bw * abs(sin)) / den, 0.1)
        dx, dy = (c["x0"] + c["x1"]) / 2 - cx, cy - (c["top"] + c["bottom"]) / 2  # y up
        mx, my = cx + dx * cos + dy * sin, cy - (-dx * sin + dy * cos)  # back to y down
        top = my - h / 2
        out.append(
            c
            | {
                "x0": mx - w / 2,
                "x1": mx + w / 2,
                "top": top,
                "bottom": top + h,
                "doctop": c["doctop"] - c["top"] + top,
                "y0": height - top - h,
                "y1": height - top,
                "width": w,
                "height": h,
                "size": round(h, 1),  # words split on any size change
                "upright": True,
            }
        )
    return out


def read_pdf(path: str | Path, deskew: bool = True) -> Document:
    path = Path(path)
    pages: list[Page] = []
    with pdfplumber.open(path) as pdf:
        for number, pg in enumerate(pdf.pages, start=1):
            chars, skew = pg.chars, 0.0
            if deskew:
                angle = estimate_skew(chars)
                if SKEW_MIN_DEG <= abs(angle) <= SKEW_MAX_DEG:
                    chars, skew = straighten(chars, angle, float(pg.width), float(pg.height)), angle
            raw = extract_words(chars, x_tolerance=2, y_tolerance=2, extra_attrs=["size"])
            words = [
                part
                for r in raw
                for part in _unglue(
                    Word(
                        r["text"],
                        float(r["x0"]),
                        float(r["x1"]),
                        float(r["top"]),
                        float(r["bottom"]),
                        float(r.get("size") or (r["bottom"] - r["top"])),
                        number,
                    )
                )
            ]
            area = float(pg.width) * float(pg.height) or 1.0
            covered = sum(
                max(0.0, min(float(im["x1"]), float(pg.width)) - max(float(im["x0"]), 0.0))
                * max(0.0, min(float(im["bottom"]), float(pg.height)) - max(float(im["top"]), 0.0))
                for im in pg.images
            )
            pages.append(
                Page(
                    number=number,
                    width=float(pg.width),
                    height=float(pg.height),
                    lines=_group_lines(words, number),
                    char_count=sum(len(w.text) for w in words),
                    image_coverage=min(1.0, covered / area),
                    rotation=int(getattr(pg, "rotation", 0) or 0),
                    skew=round(skew, 1),
                )
            )
    index = 0
    for p in pages:
        for ln in p.lines:
            ln.index = index
            index += 1
    return Document(path=path, pages=pages)
