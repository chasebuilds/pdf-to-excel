"""Optional second opinion from a local model, for low-confidence fields only.

Off by default. Talks only to an Ollama server on this machine through its
OpenAI-compatible endpoint. A model answer never clears a field from review
on its own: it fills blanks at low confidence, and it can raise confidence
only when it agrees with what the deterministic code already found.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .models import DEFAULT_THRESHOLD, SCALAR_FIELDS, Field, InvoiceResult
from .parsing import parse_date, parse_money
from .reader import Document, read_pdf

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
LLM_ONLY_CONFIDENCE = 0.5
AGREEMENT_BONUS = 0.1
Poster = Callable[[dict], dict]

PROMPT = """You read invoice text and return JSON only.
Return an object with exactly these keys: {keys}.
Dates as YYYY-MM-DD. Amounts as plain numbers with a dot decimal. Currency as a 3-letter code.
Use null when the invoice does not say. Do not guess.

Invoice text:
{text}"""


def _post(payload: dict, url: str = OLLAMA_URL, timeout: float = 120.0) -> dict[str, Any]:
    if not url.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]")):
        raise ValueError("the model fallback only talks to a local server")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (local only, checked above)
        data = json.loads(resp.read())
    if not isinstance(data, dict):
        raise ValueError("the local model server returned something other than a JSON object")
    return data


def _coerce(name: str, value: object) -> object:
    if value in (None, "", "null"):
        return None
    try:
        if name in ("invoice_date", "due_date"):
            return date.fromisoformat(str(value)[:10])
        if name in ("subtotal", "tax", "total"):
            return Decimal(str(value)).quantize(Decimal("0.01"))
    except (ValueError, InvalidOperation):
        return None
    return str(value).strip()


def _same(a: object, b: object) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return " ".join(a.lower().split()) == " ".join(b.lower().split())
    return a == b


def _grounded(name: str, value: object, doc: Document, decimal_comma: bool) -> bool:
    """Is the suggested value actually printed in the document?"""
    if name in ("subtotal", "tax", "total"):
        return any(
            (m := parse_money(text, decimal_comma)) and m.amount == value
            for ln in doc.lines
            for text in [*(c.text for c in ln.cells), *(w.text for w in ln.words)]
        )
    if name in ("invoice_date", "due_date"):
        for ln in doc.lines:
            for i in range(len(ln.words)):
                chunk = " ".join(w.text for w in ln.words[i : i + 4])
                parsed = parse_date(chunk)
                if parsed and value in (parsed.value, parsed.alt):
                    return True
        return False
    if name == "currency":
        return True  # a code is an interpretation of a symbol, not a quote
    flat = " ".join(" ".join(ln.text for ln in doc.lines).casefold().split())
    return " ".join(str(value).casefold().split()) in flat


def apply_llm_fallback(
    result: InvoiceResult,
    pdf_path: str | Path,
    model: str,
    threshold: float = DEFAULT_THRESHOLD,
    post: Poster | None = None,
) -> list[str]:
    """Ask the model about fields under the threshold. Returns the fields it touched."""
    low = [n for n in SCALAR_FIELDS if result.get(n).confidence < threshold]
    if not low or result.problem:
        return []
    doc = read_pdf(pdf_path)
    text = "\n".join(ln.text for ln in doc.lines)[:12000]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT.format(keys=", ".join(low), text=text)}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        # thinking models otherwise spend the whole budget before answering
        "reasoning_effort": "none",
    }
    try:
        reply = (post or _post)(payload)
        answer = json.loads(reply["choices"][0]["message"]["content"])
    except Exception as exc:  # the fallback is best effort and must never break a batch
        for n in low:
            result.get(n).reasons.append(f"local model not available ({type(exc).__name__})")
        return []
    touched: list[str] = []
    for name in low:
        suggestion = _coerce(name, answer.get(name)) if isinstance(answer, dict) else None
        if suggestion is None:
            continue
        current = result.get(name)
        if not _grounded(name, suggestion, doc, bool(result.decimal_comma)):
            # a model can invent a plausible value; only text that is on the page is accepted
            current.reasons.append(
                f"local model {model} suggested '{suggestion}', which is not on the page: ignored"
            )
            continue
        if current.value is None:
            result.fields[name] = Field(
                value=suggestion,
                confidence=LLM_ONLY_CONFIDENCE,
                reasons=[*current.reasons, f"value suggested by local model {model}, not verified"],
                method=f"local model {model}",
            )
        elif _same(current.value, suggestion):
            current.confidence = round(min(0.99, current.confidence + AGREEMENT_BONUS), 3)
            current.reasons.append(f"local model {model} agrees")
        else:
            current.reasons.append(f"local model {model} suggests '{suggestion}' instead")
        touched.append(name)
    return touched
