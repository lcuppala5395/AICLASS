"""Document Tool: extract clean text from an uploaded PDF (deterministic, no LLM)."""
from __future__ import annotations

import io
import re
import unicodedata

from pypdf import PdfReader

MAX_CHARS = 60_000   # protects the LLM context window and cost
MIN_CHARS = 200      # below this the PDF is treated as unreadable (e.g. scanned images)


class DocumentError(Exception):
    """The PDF cannot be used for evaluation (message is shown to the user)."""


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\x00", "")
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_text(pdf_bytes: bytes, max_chars: int = MAX_CHARS) -> dict:
    """Return {"text", "pages", "chars", "truncated", "warnings"} or raise DocumentError."""
    if not pdf_bytes or pdf_bytes[:1024].find(b"%PDF-") == -1:
        raise DocumentError("File is not a valid PDF (PDF header not found).")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted and not reader.decrypt(""):
            raise DocumentError("PDF is password-protected and cannot be read.")
        pages = [_clean(page.extract_text() or "") for page in reader.pages]
    except DocumentError:
        raise
    except Exception as exc:  # pypdf can raise many different exception types
        raise DocumentError(f"PDF could not be read ({exc.__class__.__name__}: {exc}).") from exc

    if not pages:
        raise DocumentError("PDF has no pages.")
    content_chars = sum(len(p) for p in pages)
    if content_chars < MIN_CHARS:
        raise DocumentError(
            f"Only {content_chars} characters of text could be extracted. The PDF may be "
            "scanned or image-only (OCR is not supported)."
        )

    text = "\n\n".join(f"--- Page {i} ---\n{p}" for i, p in enumerate(pages, start=1))
    warnings, truncated = [], False
    if len(text) > max_chars:
        text, truncated = text[:max_chars], True
        warnings.append(f"Document text truncated to the first {max_chars:,} characters.")
    return {"text": text, "pages": len(pages), "chars": len(text), "truncated": truncated, "warnings": warnings}
