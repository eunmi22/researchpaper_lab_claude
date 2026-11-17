"""자료유형별 정식 참고문헌 (CITE-01~04). 화면·보고서·평가가 모두 이 함수 하나를 쓴다.

형식: 제목을 닫는 큰따옴표 뒤에 쉼표.  예) 김수용, “제목”, 석사학위논문, 고려대학교 대학원, 2026년 8월.
없는 값은 [○○ 미확인]으로 표시하고 지어내지 않는다.
"""
from __future__ import annotations

FILE_VERSION = "3.2.0"

from .nanet import ARTICLE, BOOK, THESIS

FIX_FIELDS = ["title", "author", "publisher", "year", "month", "degree", "journal", "volume", "issue", "pages", "doi"]
FIELD_LABELS = {"title": "제목", "author": "저자", "publisher": "발행처·학위수여기관", "year": "발행연도", "month": "발행월",
                "degree": "학위종류(석사/박사)", "journal": "학술지명", "volume": "권", "issue": "호", "pages": "쪽(시작–끝)", "doi": "DOI"}


def effective(paper: dict) -> dict:
    """우선순위: 사용자 교정값 > 공식 상세 보완값 > API 원본 정규화값."""
    out = dict(paper)
    for layer in (paper.get("enriched") or {}, paper.get("user_fix") or {}):
        for k, v in layer.items():
            if not k.startswith("_") and str(v).strip():
                out[k] = str(v).strip()
    return out


def provenance(paper: dict, key: str) -> str:
    if str((paper.get("user_fix") or {}).get(key, "")).strip():
        return "사용자 교정"
    if str((paper.get("enriched") or {}).get(key, "")).strip():
        return "공식 상세 보완"
    return "API 원본" if str(paper.get(key, "")).strip() else "미확인"


def _v(p: dict, key: str, label: str) -> str:
    return str(p.get(key) or "").strip() or f"[{label} 미확인]"


def _date(p: dict) -> str:
    year, month = str(p.get("year") or "").strip(), str(p.get("month") or "").strip()
    if not year:
        return "[발행연월 미확인]"
    return f"{year}년 {int(month)}월" if month.isdigit() else f"{year}년"


def format_citation(paper: dict) -> str:
    p = effective(paper)
    head = f"{_v(p, 'author', '저자')}, “{_v(p, 'title', '제목')}”,"
    cat = p.get("category")
    if cat == THESIS:
        degree = f"{p['degree']}학위논문" if p.get("degree") in ("석사", "박사") else "[학위종류 미확인]"
        return f"{head} {degree}, {_v(p, 'publisher', '학위수여기관')}, {_date(p)}."
    if cat == ARTICLE:
        vol = f"제{p['volume']}권 " if p.get("volume") else ""
        iss = f"제{p['issue']}호" if p.get("issue") else ""
        voliss = (vol + iss).strip() or "[권호 미확인]"
        pages = f"pp. {p['pages']}" if p.get("pages") else "[쪽수 미확인]"
        doi = f" DOI: {p['doi']}." if p.get("doi") else ""
        return f"{head} {_v(p, 'journal', '학술지명')}, {voliss}, {pages}, {_date(p)}.{doi}"
    if cat == BOOK:
        return f"{head} {_v(p, 'publisher', '출판사')}, {str(p.get('year') or '').strip() or '[발행연도 미확인]'}."
    return f"{head} [자료유형 미확인], {_v(p, 'publisher', '발행처')}, {_date(p)}."


def missing_fields(paper: dict) -> list[str]:
    return [seg[1:-1] for seg in __import__("re").findall(r"\[[^\]]+ 미확인\]", format_citation(paper))]
