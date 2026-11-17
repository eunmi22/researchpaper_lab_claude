"""국회도서관 자료검색 서비스(/searchservice/basic) 호출·파싱·서지 정규화·자료유형 분류.

근거: 공공데이터포털 「국회도서관 자료검색 서비스」 활용가이드.
  요청변수: serviceKey, pageno, displaylines, search=[검색항목,키워드]
  응답: <response><header><resultCode/><resultMsg/></header><total/><record><item><name/><value/></item>...
명세에 없는 정렬·DB 파라미터는 추가하지 않는다 (SEARCH-02).
"""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import html
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.parse import unquote

import requests

API_BASE_URL = "https://apis.data.go.kr/9720000/searchservice/basic"
SEARCH_FIELDS = ["전체", "자료명", "저자", "발행자", "키워드", "기본검색", "청구기호"]
DETAIL_HOST = "dl.nanet.go.kr"

THESIS, ARTICLE, BOOK, UNKNOWN = "학위논문", "학술기사", "도서자료", "자료유형 미확인"
CATEGORY_ORDER = [THESIS, ARTICLE, BOOK, UNKNOWN]
# 관리번호 접두 규칙: 활용가이드 예시와 공식 상세페이지 주소에서 확인된 것만 둔다 (TYPE-02).
PREFIX_RULES = {"KDMT": THESIS, "KINX": ARTICLE, "MONO": BOOK}

GATEWAY_ERRORS = {
    "12": "서비스 경로가 없습니다 (/searchservice/basic 확인)",
    "20": "서비스 접근이 거부되었습니다 (활용신청 승인 확인)",
    "22": "일일 요청 한도를 넘었습니다",
    "30": "등록되지 않은 인증키입니다",
    "31": "인증키 사용 기간이 끝났습니다",
}


class NanetError(Exception):
    """kind: config | http | api | parse"""

    def __init__(self, message: str, kind: str = "api"):
        super().__init__(message)
        self.kind = kind


def normalize_key(key: str) -> str:
    if not key or not key.strip():
        raise NanetError("관리자 공공데이터 API 키(DATA_GO_KR_API_KEY)가 설정되지 않았습니다.", "config")
    return unquote(key.strip())


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _first(root, name: str) -> str:
    for node in root.iter():
        if _local(node.tag) == name.lower():
            return "".join(node.itertext()).strip()
    return ""


def clean_text(value) -> str:
    """표시용 HTML 태그·엔티티 정리 (META-02). 원래 값은 호출부가 raw에 보관한다."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]{1,200}>", "", text)
    return " ".join(text.replace("\x00", "").split())


def parse_search_xml(xml_data: bytes) -> tuple[int, list[dict]]:
    if len(xml_data) > 5_000_000:
        raise NanetError("API 응답이 너무 큽니다.", "parse")
    upper = xml_data[:2000].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise NanetError("지원하지 않는 XML 형식입니다.", "parse")
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise NanetError("API 응답을 XML로 해석할 수 없습니다.", "parse") from exc
    gateway = _first(root, "returnReasonCode")
    if gateway:
        raise NanetError(f"공공데이터포털 오류 {gateway}: {GATEWAY_ERRORS.get(gateway, '승인·요청 조건을 확인하세요')}", "api")
    code = _first(root, "resultCode")
    if code != "00":
        raise NanetError(f"국회도서관 응답 오류 {code or '코드 없음'}: {_first(root, 'resultMsg')[:80]}", "api")
    try:
        total = int((_first(root, "total") or "0").replace(",", ""))
    except ValueError as exc:
        raise NanetError("전체 건수(total)를 해석할 수 없습니다.", "parse") from exc
    rows = []
    for record in root.iter():
        if _local(record.tag) not in ("record", "recode", "row"):
            continue
        row: dict[str, str] = {}
        for item in record:
            if _local(item.tag) != "item":
                continue
            name = value = ""
            for child in item:
                if _local(child.tag) == "name":
                    name = "".join(child.itertext()).strip()
                elif _local(child.tag) == "value":
                    value = "".join(child.itertext()).strip()
            if name and value and name not in row:
                row[name] = value
        if row:
            rows.append(row)
    return total, rows


# ---------- 서지 정규화 ----------
_ALIASES = {
    "controlno": ("controlno", "제어번호", "관리번호"),
    "title_author": ("자료명/저자사항", "자료명저자사항", "기사명/저자명", "기사명저자명", "자료명", "기사명", "서명", "논문명", "title"),
    "publisher": ("발행자", "발행처", "발행사항"),
    "year": ("발행년", "발행년도", "발행연도"),
    "journal": ("수록지명", "수록정보", "학술지명"),
    "db": ("db", "자료유형", "자료구분"),
    "degree_note": ("학위사항", "학위논문주기"),
    "isbn": ("isbn",), "issn": ("issn",), "doi": ("doi",),
}


def _canon(name: str) -> str:
    simple = re.sub(r"[\s_\-.:()]", "", clean_text(name)).lower()
    for canon, aliases in _ALIASES.items():
        if simple in {re.sub(r"[\s_\-.:()]", "", a).lower() for a in aliases}:
            return canon
    return ""


def split_title_author(value: str) -> tuple[str, str]:
    """'제목 =대등제목 /저자1,저자2' → (제목, 저자). 구분자 ' /' 가 없으면 저자는 빈 값."""
    text = clean_text(value)
    m = re.search(r"\s/\s?(?=\S)", text)
    title, author = (text[:m.start()], text[m.end():]) if m else (text, "")
    title = re.split(r"\s=\s?(?=[A-Za-z])", title)[0]
    return title.strip(" /"), author.strip(" ;,/")


def classify(raw_db: str, controlno: str, degree_note: str = "") -> dict:
    """자료유형 자동분류 (TYPE-01~04). 근거가 충돌하면 미확인으로 남긴다."""
    db = clean_text(raw_db)
    by_db = ""
    if "학위" in db:
        by_db = THESIS
    elif "기사" in db or "학술지" in db:
        by_db = ARTICLE
    elif any(k in db for k in ("도서", "단행")):
        by_db = BOOK
    m = re.match(r"^([A-Z]{4})\d{6,20}$", controlno or "")
    by_prefix = PREFIX_RULES.get(m.group(1), "") if m else ""
    note = clean_text(degree_note) + " " + db
    degree = "박사" if "박사" in note else "석사" if "석사" in note else ""
    scope = "해외" if any(k in db for k in ("해외", "국외", "외국")) else "국내" if "국내" in db else ""
    if by_db and by_prefix and by_db != by_prefix:
        return {"category": UNKNOWN, "basis": f"충돌: DB='{db}' / 관리번호 접두 {m.group(1)}", "conflict": True, "degree": degree, "scope": scope}
    if by_db:
        return {"category": by_db, "basis": f"API 자료유형(DB)='{db}'", "conflict": False, "degree": degree, "scope": scope}
    if by_prefix:
        return {"category": by_prefix, "basis": f"관리번호 접두 {m.group(1)}", "conflict": False, "degree": degree, "scope": scope}
    reason = f"알 수 없는 유형 '{db}'" if db else "유형 정보 없음"
    return {"category": UNKNOWN, "basis": reason, "conflict": False, "degree": degree, "scope": scope}


def normalize_record(row: dict, seq: int = 0) -> dict:
    canon: dict[str, str] = {}
    for key, value in row.items():
        c = _canon(key)
        if c and not canon.get(c):
            canon[c] = clean_text(value)
    title, author = split_title_author(canon.get("title_author", ""))
    controlno = re.sub(r"\s", "", canon.get("controlno", ""))
    publisher_raw = canon.get("publisher", "")
    publisher = clean_text(re.split(r",\s*(?=\d{4})", publisher_raw)[0])
    ym = re.search(r"((?:19|20)\d{2})\.\s*(\d{1,2})", publisher_raw)
    year = canon.get("year", "") if re.fullmatch(r"(19|20)\d{2}", canon.get("year", "")) else ""
    cls = classify(canon.get("db", ""), controlno, canon.get("degree_note", ""))
    return {
        "paper_id": f"nanet:{controlno}" if controlno else f"tmp:{seq:04d}",
        "source": "국회도서관 API", "controlno": controlno,
        "title": title, "author": author, "publisher": publisher,
        "year": year, "month": ym.group(2).lstrip("0") if ym and ym.group(1) == year else "",
        "journal": canon.get("journal", ""), "doi": canon.get("doi", ""),
        "raw_type": canon.get("db", ""), "category": cls["category"], "degree": cls["degree"],
        "scope": cls["scope"], "type_basis": cls["basis"], "type_conflict": cls["conflict"],
        "volume": "", "issue": "", "pages": "", "enriched": {}, "user_fix": {}, "raw": dict(row),
    }


def detail_url(controlno: str) -> str:
    return f"https://{DETAIL_HOST}/detail/{controlno}" if re.fullmatch(r"[A-Z]{4}\d{6,20}", controlno or "") else ""


def merge_records(existing: list[dict], incoming: list[dict]) -> tuple[list[dict], int]:
    """관리번호(paper_id) 기준 병합. 제목만 같은 자료는 합치지 않는다 (SEARCH-07, DATA-01)."""
    seen = {r["paper_id"] for r in existing}
    merged, dup = list(existing), 0
    for r in incoming:
        if r["paper_id"] in seen:
            dup += 1
            continue
        seen.add(r["paper_id"])
        merged.append(r)
    return merged, dup


def sort_records(records: list[dict]) -> list[dict]:
    """유형 우선 안정 정렬. 같은 유형 안에서는 API 응답 순서를 보존한다 (SORT-01)."""
    return sorted(records, key=lambda r: CATEGORY_ORDER.index(r["category"]))


@dataclass
class SearchRun:
    keyword: str
    field: str
    per_page: int
    total: int = 0
    pages_ok: list[int] = field(default_factory=list)
    failed_page: int = 0
    records: list[dict] = field(default_factory=list)
    duplicates: int = 0
    diagnostics: list[dict] = field(default_factory=list)


def fetch_page(api_key: str, keyword: str, page: int, per_page: int, field_name: str = "전체",
               session=None, retries: int = 1) -> tuple[int, list[dict], dict]:
    """한 페이지 요청. 실패는 NanetError(kind)로 구분해 올린다 (SEARCH-04). 키는 진단에 남기지 않는다."""
    key = normalize_key(api_key)
    keyword = " ".join((keyword or "").split())
    if not keyword:
        raise NanetError("검색어를 입력해 주세요.", "config")
    if "|" in keyword:
        raise NanetError("검색어에 | 문자는 쓸 수 없습니다.", "config")
    if field_name not in SEARCH_FIELDS:
        raise NanetError("검색 대상이 공식 명세에 없습니다.", "config")
    params = {"serviceKey": key, "pageno": int(page), "displaylines": max(1, min(int(per_page), 100)),
              "search": f"{field_name},{keyword}"}
    client = session or requests
    diag = {"페이지": page, "검색항목": field_name, "실제 검색어": keyword, "페이지당": params["displaylines"], "HTTP": 0, "결과": ""}
    last = None
    for attempt in range(retries + 1):
        try:
            resp = client.get(API_BASE_URL, params=params, timeout=(6, 25))
            diag["HTTP"] = resp.status_code
            if resp.status_code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(0.4 * (attempt + 1))
                continue
            if resp.status_code == 429:
                raise NanetError("API 호출 제한(HTTP 429)에 걸렸습니다. 잠시 후 다시 시도해 주세요.", "http")
            if resp.status_code >= 400 and not resp.content.strip().startswith(b"<"):
                raise NanetError(f"HTTP {resp.status_code} 오류가 발생했습니다.", "http")
            total, rows = parse_search_xml(resp.content)
            diag["결과"] = f"정상 · 전체 {total}건 중 {len(rows)}건 수신"
            return total, rows, diag
        except requests.RequestException:
            last = NanetError("국회도서관 API에 연결하지 못했습니다 (시간 초과 또는 네트워크).", "http")
            if attempt < retries:
                time.sleep(0.4 * (attempt + 1))
                continue
        except NanetError as exc:
            last = NanetError(str(exc).replace(key, "[키 제외]"), exc.kind)
            break
    diag["결과"] = f"실패 · {last}"
    last.diag = diag  # type: ignore[attr-defined]
    raise last


def run_search(api_key: str, keyword: str, pages: int, per_page: int, field_name: str = "전체",
               session=None, run: SearchRun | None = None, start_page: int | None = None) -> SearchRun:
    """여러 페이지 수집. 뒤 페이지가 실패하면 앞 결과를 보존하고 failed_page에 기록한다 (SEARCH-08)."""
    run = run or SearchRun(keyword=" ".join(keyword.split()), field=field_name, per_page=per_page)
    first = start_page or (max(run.pages_ok) + 1 if run.pages_ok else 1)
    run.failed_page = 0
    for page in range(first, first + pages):
        try:
            total, rows, diag = fetch_page(api_key, run.keyword, page, run.per_page, run.field, session)
        except NanetError as exc:
            run.diagnostics.append(getattr(exc, "diag", {"페이지": page, "결과": f"실패 · {exc}"}))
            if not run.pages_ok:
                raise
            run.failed_page = page
            break
        run.total = total
        run.diagnostics.append(diag)
        base = len(run.records)
        normalized = [normalize_record(r, base + i + 1 + page * 1000) for i, r in enumerate(rows)]
        run.records, dup = merge_records(run.records, normalized)
        run.duplicates += dup
        run.pages_ok.append(page)
        if not rows or len(run.records) >= total:
            break
    return run


_DEGREE_RE = re.compile(r"학위논문\s*\(\s*(석사|박사)\s*\)\s*-{1,2}\s*([^,<]{2,60}),\s*([^,<]{0,60}),\s*((?:19|20)\d{2})\.\s*(\d{1,2})")


def parse_detail_html(text: str) -> dict:
    """공식 상세페이지의 '학위논문(석사) -- ○○대학교 대학원, 학과, 2026.2.' 표기에서 학위정보를 읽는다."""
    m = _DEGREE_RE.search(clean_text(text))
    if not m:
        return {}
    return {"degree": m.group(1), "publisher": m.group(2).strip(), "department": m.group(3).strip(),
            "year": m.group(4), "month": m.group(5).lstrip("0")}


def fetch_detail(controlno: str, session=None) -> dict:
    """허용 도메인·리다이렉트 차단·타임아웃을 적용한 상세 보완 (META-03~05). 실패하면 빈 값."""
    url = detail_url(controlno)
    if not url:
        return {}
    try:
        resp = (session or requests).get(url, timeout=(5, 10), allow_redirects=False,
                                         headers={"User-Agent": "GradPaperHelper/3.0 (academic metadata)"})
        if resp.status_code != 200 or len(resp.content) > 2_000_000:
            return {}
        found = parse_detail_html(resp.text)
        if found:
            found["_source"] = url
            found["_checked_at"] = time.strftime("%Y-%m-%d %H:%M")
        return found
    except requests.RequestException:
        return {}
