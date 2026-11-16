import io
import json
import zipfile
import xml.etree.ElementTree as ET

import pytest
import requests

from core import cite, evalq, exporters, nanet, pdfio, store
from tests import sample as S

KEY = "TESTKEY-not-real-0123456789"


def norm(xml_record):
    _, rows = nanet.parse_search_xml(S.response(1, [xml_record]))
    return nanet.normalize_record(rows[0], 1)


# AT-01/BASE-04: 검색은 OpenAI 키와 무관 — fetch_page는 공공데이터 키만 받는다
def test_search_params_match_official_spec():
    sess = S.FakeSession({1: S.response(1, [S.THESIS])})
    nanet.fetch_page(KEY, "  데이터베이스   포렌식 ", 1, 20, "전체", session=sess)
    assert sess.calls[0] == {"serviceKey": KEY, "pageno": 1, "displaylines": 20, "search": "전체,데이터베이스 포렌식"}


# AT-02 / SEARCH-04
def test_errors_are_distinguished_from_zero_results():
    total, rows = nanet.parse_search_xml(S.response(0, []))
    assert (total, rows) == (0, [])
    with pytest.raises(nanet.NanetError) as e:
        nanet.parse_search_xml(S.response(0, [], code="99"))
    assert e.value.kind == "api"
    with pytest.raises(nanet.NanetError) as e:
        nanet.parse_search_xml(b"<html>not xml")
    assert e.value.kind == "parse"
    gw = b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    with pytest.raises(nanet.NanetError, match="등록되지 않은"):
        nanet.parse_search_xml(gw)
    with pytest.raises(nanet.NanetError) as e:
        nanet.fetch_page("", "x", 1, 10)
    assert e.value.kind == "config"


def test_key_never_appears_in_errors_or_diagnostics():
    sess = S.FakeSession({1: requests.ConnectionError(f"https://x?serviceKey={KEY}")})
    with pytest.raises(nanet.NanetError) as e:
        nanet.fetch_page(KEY, "x", 1, 10, session=sess, retries=0)
    assert KEY not in str(e.value) and KEY not in json.dumps(e.value.diag, ensure_ascii=False)


def test_xxe_rejected():
    with pytest.raises(nanet.NanetError):
        nanet.parse_search_xml(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><response/>')


# AT-04 / META-01,02
def test_title_author_html_cleaned():
    r = norm(S.THESIS)
    assert r["title"] == "합성 학위논문 제목 포렌식" and r["author"] == "홍길동"
    assert r["publisher"] == "가상대학교 대학원" and r["year"] == "2026" and r["month"] == ""
    assert "<b>" in r["raw"]["자료명/저자사항"] or "&lt;b&gt;" in r["raw"]["자료명/저자사항"]
    a = norm(S.ARTICLE)
    assert a["author"] == "김철수,이영희" and a["month"] == "6"
    # 발행자 칸 날짜의 연도와 발행년이 다르면 월을 지어내지 않는다 (활용가이드 예시에 이런 자료가 있음)
    assert norm(S.ARTICLE.replace("<value>2025</value>", "<value>2026</value>"))["month"] == ""


# AT-05, AT-06 / TYPE
def test_classification():
    assert norm(S.THESIS)["category"] == nanet.THESIS
    assert norm(S.BOOK)["category"] == nanet.BOOK
    c = norm(S.CONFLICT)
    assert c["category"] == nanet.UNKNOWN and c["type_conflict"] and "충돌" in c["type_basis"]
    n = norm(S.NOTYPE)
    assert n["category"] == nanet.UNKNOWN  # 제목의 '논문' 단어만으로 단정하지 않는다
    assert norm(S.NOID)["paper_id"].startswith("tmp:")


# AT-07 / SORT-01
def test_sort_is_stable_by_type():
    recs = [norm(x) for x in (S.BOOK, S.ARTICLE, S.THESIS, S.NOTYPE, S.ARTICLE.replace("0002", "0009"))]
    out = nanet.sort_records(recs)
    assert [r["category"] for r in out] == [nanet.THESIS, nanet.ARTICLE, nanet.ARTICLE, nanet.BOOK, nanet.UNKNOWN]
    assert [r["controlno"] for r in out if r["category"] == nanet.ARTICLE] == ["KINX2026000002", "KINX2026000009"]


# AT-03, AT-08 / SEARCH-06~08
def test_paging_merge_and_failed_page_keeps_results():
    sess = S.FakeSession({1: S.response(5, [S.THESIS, S.ARTICLE]), 2: S.response(5, [S.ARTICLE, S.BOOK]), 3: requests.Timeout()})
    run = nanet.run_search(KEY, "포렌식", 3, 2, session=sess)
    assert run.total == 5 and len(run.records) == 3 and run.duplicates == 1
    assert run.pages_ok == [1, 2] and run.failed_page == 3
    sess.pages[3] = S.response(5, [S.NOTYPE])
    nanet.run_search(KEY, "포렌식", 1, 2, session=sess, run=run, start_page=3)
    assert len(run.records) == 4 and run.failed_page == 0 and run.pages_ok == [1, 2, 3]
    assert all(c["search"] == "전체,포렌식" for c in sess.calls)  # 같은 조건으로만 추가 요청


def test_first_page_failure_raises():
    with pytest.raises(nanet.NanetError):
        nanet.run_search(KEY, "포렌식", 2, 10, session=S.FakeSession({1: S.FakeResp(b"oops", 500), 2: b""}))


def test_same_title_not_merged():
    a, b = norm(S.ARTICLE), norm(S.ARTICLE.replace("KINX2026000002", "KINX2026000777"))
    merged, dup = nanet.merge_records([a], [b])
    assert len(merged) == 2 and dup == 0


# AT-24 / CITE
def test_citation_formats_and_missing_values():
    t = norm(S.THESIS)
    assert cite.format_citation(t) == "홍길동, “합성 학위논문 제목 포렌식”, [학위종류 미확인], 가상대학교 대학원, 2026년."
    t["enriched"] = nanet.parse_detail_html("<p>학위논문(석사) -- 가상대학교 대학원, 정보보안학과, 2026.8. 지도교수: 아무개</p>")
    assert cite.format_citation(t) == "홍길동, “합성 학위논문 제목 포렌식”, 석사학위논문, 가상대학교 대학원, 2026년 8월."
    t["user_fix"] = {"author": "홍 길동"}
    assert cite.format_citation(t).startswith("홍 길동, “") and cite.provenance(t, "author") == "사용자 교정" and t["author"] == "홍길동"
    a = cite.format_citation(norm(S.ARTICLE))
    assert "”, 가상학회지" in a and "[권호 미확인]" in a and "[쪽수 미확인]" in a and "DOI" not in a
    assert cite.format_citation(norm(S.BOOK)) == "박저자, “합성 도서”, 가상출판사, 2024."


def test_user_confirmed_example():
    p = {"title": "Amazon RDS 데이터베이스 디지털포렌식 조사 기법 연구", "author": "김수용", "category": nanet.THESIS, "degree": "석사",
         "publisher": "고려대학교 대학원", "year": "2026", "month": "8"}
    assert cite.format_citation(p) == "김수용, “Amazon RDS 데이터베이스 디지털포렌식 조사 기법 연구”, 석사학위논문, 고려대학교 대학원, 2026년 8월."


def test_detail_url_only_for_valid_controlno():
    assert nanet.detail_url("KDMT1202600000001") == "https://dl.nanet.go.kr/detail/KDMT1202600000001"
    assert nanet.detail_url("../../etc") == "" and nanet.detail_url("") == ""


# AT-34 / DATA-02,03 + NFR-01
def test_project_export_excludes_secrets_and_fulltext():
    p = store.new_project()
    p["docs"]["x"] = {"document_id": "abc", "name": "a.pdf", "pages": ["본문 전체"]}
    p["notes"]["x"] = store.empty_note() | {"my_thoughts": "내 생각 sk-abcdefghijklmnopqrstuvwxyz123456"}
    raw = store.export_project(p)
    assert b"sk-abcdef" not in raw and "본문 전체".encode() not in raw
    back, notes = store.import_project(raw)
    assert "내 생각" in back["notes"]["x"]["my_thoughts"] and back["docs"]["x"]["pdf_included"] is False and notes
    assert p["docs"]["x"]["pages"] == ["본문 전체"]  # 원본 세션은 그대로
    for bad in (b"not json", b"{}", json.dumps({"schema_version": 99, "papers": {}}).encode()):
        with pytest.raises(store.ImportError_):
            store.import_project(bad)


# NFR-07
def test_formula_strings_neutralized():
    assert store.safe_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and store.safe_cell("정상") == "정상" and store.safe_cell(3) == 3
    assert b"'=1+1" in exporters.to_csv([{"a": "=1+1"}])
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(exporters.to_xlsx({"s": [{"a": "@cmd"}]}))).active
    assert ws["A2"].value == "'@cmd"


def _project_with_paper():
    p = store.new_project()
    r = norm(S.THESIS)
    p["papers"][r["paper_id"]] = r
    p["notes"][r["paper_id"]] = store.empty_note() | {"my_thoughts": "로그가 꺼져 있으면 어떻게 추적하나?", "selection_reason": "관심 주제"}
    return p, r["paper_id"]


# AT-20 / REPORT
def test_report_blocks_docx_hwpx():
    p, pid = _project_with_paper()
    p["notes"][pid]["method"] = {"three_lines": {"기여": "절차를 제안했다.", "증명방법": "단계별 비교를 했다.", "검증 범위": "실험 수치는 없다."}}
    p["notes"][pid]["edited"] = {"contribution": "내가 고친 기여 문장"}
    blocks = exporters.report_blocks(p, pid)
    flat = " ".join(t for _, t in blocks)
    assert cite.format_citation(p["papers"][pid]) in flat and "내가 고친 기여 문장" in flat and "절차를 제안했다." not in flat
    assert "직접 재현·실험한 결과는 포함하지 않습니다" in flat and "로그가 꺼져 있으면" in flat
    from docx import Document
    text = "\n".join(x.text for x in Document(io.BytesIO(exporters.to_docx(blocks))).paragraphs)
    assert "단계별 비교를 했다." in text and "[학위종류 미확인]" in text
    hw = zipfile.ZipFile(io.BytesIO(exporters.to_hwpx(blocks)))
    assert hw.namelist()[0] == "mimetype" and hw.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
    sec = hw.read("Contents/section0.xml").decode()
    header = hw.read("Contents/header.xml").decode()
    ET.fromstring(sec)
    assert "내가 고친 기여 문장" in sec and "secPr" in sec
    import re
    ids = sec.split("</hp:p>", 1)[1]
    for attr, tag in (("charPrIDRef", "charPr"), ("paraPrIDRef", "paraPr")):
        for ref in set(re.findall(attr + r'="(\d+)"', ids)):
            assert re.search(rf'<hh:{tag} id="{ref}"', header), f"{tag} {ref} 정의 없음"
    assert len(set(re.findall(r'<hp:p id="(\d+)"', sec))) == len(re.findall(r'<hp:p id="(\d+)"', sec))


def test_hwpx_escapes_special_characters():
    ET.fromstring(zipfile.ZipFile(io.BytesIO(exporters.to_hwpx([("p", "a < b & c > d")]))).read("Contents/section0.xml"))


# AT-14~16 / READ
def test_pdf_errors_and_payload():
    with pytest.raises(pdfio.PdfError):
        pdfio.extract(b"not a pdf")
    pages = ["가" * 3000, "", "나" * 200, "다" * 3000]
    text, full, partial = pdfio.build_payload(pages, budget_chars=3000)
    assert full == [3] and partial == [1, 4] and "[PDF p.4 · 일부]" in text and 2 not in full + partial
    text, full, partial = pdfio.build_payload(pages, budget_chars=10_000)
    assert full == [1, 3, 4] and partial == []


def test_quote_verification():
    pages = ["클라우드 데이터베이스의 활용이 증가하고 있으나", "삭제 레코드 복구는 수행할 수 없다."]
    assert pdfio.verify_quote(pages, 2, "삭제 레코드  복구는 수행할 수 없다") == "일치"
    assert pdfio.verify_quote(pages, 1, "삭제 레코드 복구는 수행할 수 없다") == "다른 쪽에서 일치(p.2)"
    assert pdfio.verify_quote(pages, 1, "성능이 37% 향상되었다는 문장") == "불일치"
    assert pdfio.verify_quote(pages, 1, "") == "문구 없음"


# AT-17, AT-18, AT-25~27, AT-29, AT-30 / EVAL
def _items(names):
    return {"items": {n: {"text": f"{n} 문장이다.", "pages": [1], "quote": "클라우드 데이터베이스의 활용"} for n in names}}


def test_format_and_rule_checks():
    from core.ai import SEVEN, SIX
    assert evalq.format_check("summary6", _items(SIX))["state"] == "기준 충족"
    assert evalq.format_check("summary6", _items(SIX[:5]))["state"] == "기준 미달"
    assert evalq.format_check("summary7", _items(SIX))["state"] == "기준 미달"  # 모드가 섞이면 통과하지 않는다
    assert evalq.format_check("summary7", _items(SEVEN))["state"] == "기준 충족"
    case = evalq.new_case(task_type="summary6", response=_items(SIX) | {}, provided_contexts=["클라우드 데이터베이스의 활용이 증가"])
    case["response"]["items"]["주제"]["text"] = "성능이 37% 향상되었다."
    res = evalq.rule_checks(case, ["클라우드 데이터베이스의 활용이 증가하고 있으나"])
    assert res["수치 근거"]["state"] == "검토 필요" and "37%" in res["수치 근거"]["detail"]
    assert res["근거 문구 대조"]["state"] == "기준 충족" and res["필수 내용 포함"]["state"] == "미평가"
    assert evalq.rule_checks(case, None)["근거 문구 대조"]["state"] == "미평가"


def test_preflight_blocks_metrics_without_inputs():
    c1 = evalq.new_case(response="답", provided_contexts=[], reference="AI가 쓴 기준", review_state="검토 필요")
    c2 = evalq.new_case(response="답", provided_contexts=["근거"], reference="사람이 확인", review_state="검토 완료")
    pf = evalq.preflight([c1, c2], ["충실도(RAGAS)", "기준 답변 일치(RAGAS)"])
    assert pf[0]["충실도(RAGAS)"].startswith("실행 불가") and pf[0]["기준 답변 일치(RAGAS)"].startswith("실행 불가")
    assert pf[1]["충실도(RAGAS)"] == "실행 가능" and pf[1]["기준 답변 일치(RAGAS)"] == "실행 가능"


def test_failed_evaluation_is_not_zero(monkeypatch):
    from ragas.metrics.collections import Faithfulness

    async def boom(self, **kw):
        raise TimeoutError("provider down")

    monkeypatch.setattr(Faithfulness, "ascore", boom)
    monkeypatch.setattr("ragas.llms.llm_factory", lambda *a, **k: None)
    monkeypatch.setattr(Faithfulness, "__init__", lambda self, llm=None: None)
    case = evalq.new_case(response="답변", provided_contexts=["근거"])
    run = evalq.run_ragas([case], ["충실도(RAGAS)"], "sk-test", "gpt-4.1-mini", 0.8)
    cell = run["results"][0]["충실도(RAGAS)"]
    assert run["state"] == "오류" and cell["score"] is None and cell["state"] == "미평가"
    assert evalq.summarize_run(run)[0]["평균"] == "유효 평가 없음"
    assert "sk-test" not in json.dumps(run, ensure_ascii=False)


def test_successful_ragas_path_with_stub(monkeypatch):
    from ragas.metrics.collections import Faithfulness

    class R:
        value = 0.5

    async def fine(self, **kw):
        assert set(kw) == {"user_input", "response", "retrieved_contexts"}
        return R()

    monkeypatch.setattr(Faithfulness, "ascore", fine)
    monkeypatch.setattr("ragas.llms.llm_factory", lambda *a, **k: None)
    monkeypatch.setattr(Faithfulness, "__init__", lambda self, llm=None: None)
    cases = [evalq.new_case(response="답변", provided_contexts=["근거"]), evalq.new_case(response="답변", provided_contexts=[])]
    run = evalq.run_ragas(cases, ["충실도(RAGAS)"], "k", "m", 0.8)
    assert run["state"] == "완료" and run["results"][0]["충실도(RAGAS)"] == {"state": "검토 필요", "score": 0.5, "detail": ""}
    assert run["results"][1]["충실도(RAGAS)"]["state"] == "해당 없음"
    s = evalq.summarize_run(run)[0]
    assert (s["유효 평가"], s["해당 없음"], s["평균"]) == (1, 1, 0.5)
    run2 = json.loads(json.dumps(run))
    run2["conditions"]["eval_model"] = "other"
    run2["results"][0]["충실도(RAGAS)"]["score"] = 0.9
    cmp_ = evalq.compare_runs(run, run2)
    assert not cmp_["same_conditions"] and cmp_["different"] == ["eval_model"] and cmp_["rows"][0]["변화"] == "개선"


def test_uploaded_cases_validated():
    ok, problems = evalq.validate_cases([{"response": "답", "reference": "기준", "review_state": "검토 완료", "provided_contexts": "문단1||문단2"},
                                         {"reference": "답변 없음"}, "문자열"])
    assert len(ok) == 1 and len(problems) == 2 and ok[0]["provided_contexts"] == ["문단1", "문단2"] and ok[0]["review_state"] == "검토 완료"


def test_report_template_order_labels_and_sections():
    from core import reporttpl
    p, pid = _project_with_paper()
    p["notes"][pid]["summary7"] = {"data": {"items": {"연구 배경": {"text": "배경 문장."}}}}
    tpl = p["project"]["report_tpl"]
    assert [b[1] for b in exporters.report_blocks(p, pid) if b[0] == "h"][1] == "2. 선정 이유"
    tpl["sections"] = reporttpl.preset_sections("상세", tpl["sections"])
    tpl["title"] = "Deep Dive Topic 보고서"
    tpl["sections"][0]["label"] = "참고 논문"
    blocks = exporters.report_blocks(p, pid)
    heads = [t for k, t in blocks if k == "h"]
    assert blocks[0] == ("title", "Deep Dive Topic 보고서") and heads[0] == "1. 참고 논문" and heads[2] == "3. 논문 요약 (7문장)"
    assert ("b", "연구 배경|배경 문장.") in blocks and ("b", "연구 질문|[작성 필요]") in blocks
    assert any(t == "[따라 해 보기 검토 작성 필요]" for _, t in blocks)
    tpl["sections"] = reporttpl.preset_sections("간단", tpl["sections"])
    heads = [t for k, t in exporters.report_blocks(p, pid) if k == "h"]
    assert heads == ["1. 참고 논문", "2. 연구 기여도와 증명방법 (핵심 3줄)", "3. 나의 생각·의문"]  # 바꾼 제목은 양식을 바꿔도 유지
    p["project"]["report_tpl"] = {"sections": "깨진 값"}
    assert [t for k, t in exporters.report_blocks(p, pid) if k == "h"][0] == "1. 서지정보"  # 잘못된 양식은 기본으로


def test_old_project_file_gets_new_fields():
    p, pid = _project_with_paper()
    raw = json.loads(store.export_project(p))
    del raw["project"]["report_tpl"]
    for k in ("page_notes", "memos", "chat", "questions3"):
        raw["notes"][pid].pop(k, None)
    back, _ = store.import_project(json.dumps(raw).encode())
    assert back["project"]["report_tpl"]["preset"] == "기본" and back["notes"][pid]["chat"] == [] and back["notes"][pid]["page_notes"] == {}
