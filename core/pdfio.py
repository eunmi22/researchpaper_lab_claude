"""PDF 텍스트 추출과 AI 전송 범위 구성 (READ-03~06). OCR과 그림 직접 분석은 지원하지 않는다."""
from __future__ import annotations

import hashlib
import re

MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_PAGES = 400
MIN_CHARS_PER_PAGE = 40


class PdfError(Exception):
    pass


def extract(pdf_bytes: bytes, name: str = "") -> dict:
    import pymupdf as fitz

    if not pdf_bytes.startswith(b"%PDF"):
        raise PdfError("PDF 파일이 아닙니다.")
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise PdfError(f"파일이 너무 큽니다 (최대 {MAX_PDF_BYTES // 1024 // 1024}MB).")
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # 손상 파일
        raise PdfError("PDF를 열 수 없습니다. 파일이 손상되었을 수 있습니다.") from exc
    if doc.needs_pass:
        raise PdfError("암호가 걸린 PDF입니다. 암호를 푼 파일을 올려 주세요.")
    if doc.page_count > MAX_PAGES:
        raise PdfError(f"쪽수가 너무 많습니다 (최대 {MAX_PAGES}쪽).")
    pages, empty = [], []
    for i, page in enumerate(doc, start=1):
        text = re.sub(r"[ \t]+", " ", page.get_text("text") or "").strip()
        pages.append(text)
        if len(text) < MIN_CHARS_PER_PAGE:
            empty.append(i)
    captions = []
    for i, text in enumerate(pages, start=1):
        for m in re.finditer(r"^\s*((?:그림|표|Figure|Fig\.|Table)\s*\d+[.:]?\s+[^\n.]{3,90})", text, flags=re.M):
            cap = " ".join(m.group(1).split())
            if "··" not in cap and "…" not in cap:
                captions.append({"page": i, "caption": cap})
    last = {}
    for c in captions:  # 목차에 한 번 더 나오는 캡션은 본문 쪽의 것만 남긴다
        last[re.match(r"\D+\d+", c["caption"]).group(0).replace(" ", "")] = c
    captions = list(last.values())
    total_chars = sum(len(t) for t in pages)
    status = "ok"
    if total_chars < MIN_CHARS_PER_PAGE * max(1, doc.page_count) * 0.3:
        status = "scanned"  # 텍스트가 거의 없음 → 스캔본 가능성
    return {"document_id": hashlib.sha256(pdf_bytes).hexdigest()[:16], "name": name, "page_count": doc.page_count,
            "pages": pages, "empty_pages": empty, "captions": captions, "total_chars": total_chars, "status": status}


def build_payload(pages: list[str], budget_chars: int = 90_000) -> tuple[str, list[int], list[int]]:
    """AI에 보낼 본문. 예산 안이면 전체, 넘으면 앞·뒤와 중간을 고르게 덜어 보낸다.

    반환: (본문, 전부 보낸 쪽, 일부만 보낸 쪽). 텍스트가 없는 쪽은 어느 쪽에도 넣지 않는다.
    """
    idx = [i for i, t in enumerate(pages, start=1) if t.strip()]
    total = sum(len(pages[i - 1]) for i in idx)
    full, partial, parts = [], [], []
    if total <= budget_chars:
        for i in idx:
            parts.append(f"[PDF p.{i}]\n{pages[i - 1]}")
            full.append(i)
        return "\n\n".join(parts), full, partial
    per_page = max(600, budget_chars // max(1, len(idx)))
    head = max(600, per_page // 2)
    for i in idx:
        text = pages[i - 1]
        if len(text) <= per_page:
            parts.append(f"[PDF p.{i}]\n{text}")
            full.append(i)
        else:
            parts.append(f"[PDF p.{i} · 일부]\n{text[:head]}\n(…중략…)\n{text[-(per_page - head):]}")
            partial.append(i)
    return "\n\n".join(parts), full, partial


def _norm(s: str) -> str:
    return re.sub(r"[\s·･ㆍ·ㆍ･'\"“”‘’]", "", s or "").lower()


def verify_quote(pages: list[str], page_no, quote: str) -> str:
    """근거 문구가 추출 텍스트에 실제로 있는지 대조 (EVIDENCE-03). 문자열 일치일 뿐 해석의 정확성은 보증하지 않는다."""
    q = _norm(quote)
    if len(q) < 8:
        return "문구 없음"
    try:
        n = int(page_no)
    except (TypeError, ValueError):
        n = 0
    if 1 <= n <= len(pages) and q in _norm(pages[n - 1]):
        return "일치"
    for i, text in enumerate(pages, start=1):
        if q in _norm(text):
            return f"다른 쪽에서 일치(p.{i})"
    return "불일치"
