"""v3.2.0: 페이지별 읽기(튜터), AI 문장 완성, 연구 개요, 나의 연구 설계안, 표가 든 Word·한글."""
import io
import re
import zipfile

from core import ai, design, exporters, pdfio, tutor
from tests import sample as S
from tests.test_app import PDF, app, click, errors, search


def test_detect_printed_and_label():
    pages = ["표지", "목차"] + [f"본문 {i}\n...\n{i}" for i in range(1, 8)]
    pr = tutor.detect_printed(pages)
    assert pr["auto"] == 2 and pr["support"] >= 3
    assert tutor.page_label(pr, 5) == "PDF 5쪽 / 인쇄 3쪽"
    assert tutor.page_label(pr, 1) == "PDF 1쪽 / 인쇄 쪽수 확인되지 않음"
    pr.update(offset=2, confirmed=True)
    assert tutor.printed_of(pr, 2) is None and tutor.printed_of(pr, 9) == "7"


def test_feedback_done_rule_and_fill_norm():
    fb, done = tutor.norm_feedback({"correct": ["좋음"], "missing": [{"text": "x", "required": False}], "fix": [], "done": True})
    assert done and fb["missing"][0]["required"] is False
    _, done = tutor.norm_feedback({"missing": [{"text": "핵심", "required": True}], "done": True})
    assert not done
    assert tutor.norm_fill({"items": [], "filled": ""}) is None
    f = tutor.norm_fill({"items": [{"ask": "목적", "answer": "절차 제안", "quote": "q"}], "filled": "목적은 절차 제안", "summary": "s"})
    assert f["items"][0]["answer"] == "절차 제안"
    assert tutor.merge_memo("내 생각", "요약") == "내 생각\n[내 요약] 요약" and tutor.merge_memo("", "요약") == "요약"


def test_design_blocks_tables_numbered_and_export():
    d = design.new_design()
    d["title"] = "설계안"
    d["ch"]["theory"]["draft"] = design.norm_draft({"sections": [{"title": "4. 법·제도", "paragraphs": ["문단"],
                                                                  "table": {"caption": "표 9. 근거", "header": ["구분", "근거"], "rows": [["법", "개인정보 보호법"], ["x"]]}}]})
    d["ch"]["design"]["draft"] = design.norm_draft({"sections": [{"title": "2. 비교", "paragraphs": ["p"], "table": {"caption": "비교", "header": ["a", "b"], "rows": []}}]})
    b = design.blocks(d, ["김수용 (2026). Amazon RDS ..."])
    caps = [t for k, t in b if k == "cap"]
    assert caps == ["표 1. 근거", "표 2. 비교"]
    assert [t for k, t in b if k == "table"][0]["rows"][1] == ["x", ""]
    assert ("ref", "[1] 김수용 (2026). Amazon RDS ...") in b
    hw = exporters.to_hwpx(b, "설계안")
    sec = zipfile.ZipFile(io.BytesIO(hw)).read("Contents/section0.xml").decode()
    assert sec.count("<hp:tbl ") == 2 and "개인정보 보호법" in sec and 'rowCnt="3"' in sec
    ids = re.findall(r'<hp:p id="(\d+)"', sec) + re.findall(r'<hp:tbl id="(\d+)"', sec)
    assert len(ids) == len(set(ids))
    from docx import Document
    doc = Document(io.BytesIO(exporters.to_docx(b)))
    assert len(doc.tables) == 2 and doc.tables[0].cell(1, 1).text == "개인정보 보호법"


def _tutor_fake(calls):
    def fake(api_key, model, system, prompt, task="custom"):
        calls.append((task, prompt))
        data = {
            "tutor_q": {"range": "1장 첫 문단", "role": "연구 배경", "continues": "",
                        "questions": [{"q": "저자가 말하는 문제는?", "where": "1.1", "hint": "‘정립되지 않았다’를 찾기",
                                       "steps": ["1.1 첫 문단", "‘아직’에 주목", "____ 기법은 정립되지 않았다", ""]}]},
            "tutor_hint4": {"filled": "클라우드 DB 포렌식 기법은 정립되지 않았다", "note": ""},
            "tutor_check": {"checks": [{"verdict": "일부 맞음", "comment": "대상이 클라우드 DB임을 더해 보세요."}]},
            "tutor_help": {"frame": "배경: [ ____ ] → 목적: [ ____ ]"},
            "tutor_fill": {"items": [{"ask": "목적", "answer": "RDS 조사 절차 제안", "quote": "", "where": "1.2"}],
                           "filled": "배경: 클라우드 확산 → 목적: RDS 조사 절차 제안", "summary": "클라우드 DB 포렌식 기법이 없어 RDS 조사 절차를 제안한다.", "note": ""},
            "tutor_sumfb": {"correct": [{"text": "목적 정확", "where": "PDF 11쪽"}], "missing": [], "fix": [], "typos": [], "done": True},
            "tutor_model": {"text": "모범 요약"},
            "page_answer": {"answer": "근거 답", "quote": ""},
            "polish": {"text": "다듬은 문장이다."},
            "overview": {"text": "페이지 요약으로 만든 연구 개요이다."},
        }[task]
        return {"generation_id": "g", "task": task, "model": model, "prompt_version": "p2", "data": data, "created": "t", "usage": {}}
    return fake


def test_tutor_flow_memo_and_overview(monkeypatch):
    at = app(monkeypatch)
    search(at, monkeypatch, {1: S.response(1, [S.THESIS])})
    click(at, "02. 논문 읽기 시작")
    pid = at.session_state.current_pid
    raw = open(PDF, "rb").read()
    doc = pdfio.extract(raw, "rds.pdf")
    at.session_state.texts[pid] = doc["pages"]
    at.session_state.pdfs[pid] = raw
    at.session_state.proj["docs"][pid] = {k: doc[k] for k in ("document_id", "name", "page_count", "empty_pages", "captions", "total_chars", "status")}
    at.run()
    assert not errors(at)
    assert any(e.label == "이 쪽 글자로 보기" for e in at.expander)  # 쪽 그림이 그려졌을 때만 나오는 글자 보기
    assert next(b for b in at.button if b.label == "이 쪽 질문 받기").disabled  # 키 없으면 꺼짐
    calls = []
    monkeypatch.setattr(ai, "run_prompt", _tutor_fake(calls))
    at.text_input(key="openai_key").set_value("sk-test-key-for-ui").run()
    note = at.session_state.proj["notes"][pid]
    note["tutor"]["cur"] = 11
    at.run()
    click(at, "이 쪽 질문 받기")
    assert not errors(at) and "PDF 11쪽" in calls[-1][1]
    for _ in range(5):
        click(at, next(b.label for b in at.button if b.label.startswith("힌트")))
    md = " ".join(x.value for x in at.markdown)
    assert "‘정립되지 않았다’를 찾기" in md and "____ 기법은 정립되지 않았다" in md
    click(at, "④ 빈칸을 채운 문장 보기")
    assert "클라우드 DB 포렌식 기법은 정립되지 않았다" in " ".join(x.value for x in at.markdown)
    next(t for t in at.text_area if t.label == "Q1 내 답").set_value("포렌식 기법이 없다").run()
    click(at, "내 답 확인받기")
    assert any("일부 맞음" in x.value for x in at.markdown)
    click(at, "요약 틀 도움")
    click(at, "틀 채우기 (원문에서 답 찾아 보여 주기)")
    md = " ".join(x.value for x in at.markdown)
    assert "RDS 조사 절차 제안" in md
    click(at, "이 요약을 내 요약 칸에 넣기")
    assert next(t for t in at.text_area if t.label == "내 요약").value.startswith("클라우드 DB")
    click(at, "요약 피드백 받기")
    ps = note["tutor"]["pages"]["11"]
    assert not errors(at) and ps["done"] and note["memos"]["11"].startswith("클라우드 DB")
    assert ps["model"] == ""  # 모범 요약은 누르기 전엔 없음
    # 연구 개요: 끝낸 요약으로 만든다
    click(at, "페이지별 읽기에서 끝낸 내 요약으로 연구 개요 만들기")
    assert calls[-1][0] == "overview" and "[PDF p.11] 클라우드 DB" in calls[-1][1]
    assert note["edited"]["overview"] == "페이지 요약으로 만든 연구 개요이다."
    assert next(t for t in at.text_area if t.label == "연구 개요").value == "페이지 요약으로 만든 연구 개요이다."
    # 나의 생각: 메모를 AI로 완성 → 사용 → 되돌리기
    reason = next(t for t in at.text_area if t.label == "선정 이유")
    reason.set_value("실무에서 RDS 로그를 다뤄 봤다").run()
    click(at, "AI로 완성하기")
    assert calls[-1][0] == "polish" and "실무에서 RDS 로그를 다뤄 봤다" in calls[-1][1]
    assert note["selection_reason"] == "실무에서 RDS 로그를 다뤄 봤다"  # 누르기만 해서는 안 바뀜
    click(at, "이 문장 사용")
    assert note["selection_reason"] == "다듬은 문장이다."
    click(at, "AI 문장 넣기 전으로 되돌리기")
    assert note["selection_reason"] == "실무에서 RDS 로그를 다뤄 봤다" and not errors(at)
    assert "sk-test" not in str(at.session_state.proj)


def test_design_tab_interview_draft_download(monkeypatch):
    at = app(monkeypatch)
    search(at, monkeypatch, {1: S.response(1, [S.THESIS])})
    calls = []

    def fake(api_key, model, system, prompt, task="custom"):
        calls.append((task, prompt))
        data = {"design_q": {"questions": [{"q": "왜 RDS 로그인가?", "why": "배경", "example": "예: ..."}]},
                "design_draft": {"sections": [{"title": "", "paragraphs": ["서론 문단이다.", "[확인 필요: 범위]"], "table": None}], "needs": ["범위"]},
                "design_abs": {"abstract": ["초록1", "초록2"], "keywords": ["RDS", "포렌식"], "titles": ["제목 후보 A"]}}[task]
        return {"generation_id": "g", "task": task, "model": model, "prompt_version": "p2", "data": data, "created": "t", "usage": {}}

    monkeypatch.setattr(ai, "run_prompt", fake)
    click(at, "02. 논문 읽기 시작")
    at.sidebar.radio[0].set_value(next(o for o in at.sidebar.radio[0].options if o.startswith("04"))).run()
    assert not errors(at)
    click(at, "기본 질문으로 시작 (AI 없이)")
    assert len([t for t in at.text_area if t.label.endswith(" 답")]) == 4
    at.text_input(key="openai_key").set_value("sk-test-key-for-ui").run()
    click(at, "질문 다시 받기")
    click(at, "AI 인터뷰 질문 받기")
    assert "<참고문헌 목록>\n[1]" in calls[-1][1]
    next(t for t in at.text_area if t.label == "Q1 답").set_value("감사 실무에서 로그 공백을 겪음").run()
    click(at, "이 장 초안 만들기")
    assert "감사 실무에서 로그 공백을 겪음" in calls[-1][1]
    d = at.session_state.proj["design"]
    assert d["ch"]["intro"]["draft"]["sections"][0]["paragraphs"][0] == "서론 문단이다."
    click(at, "초록·주제어·제목 후보 만들기")
    click(at, "이 제목 쓰기")
    assert d["title"] == "제목 후보 A" and d["abs"]["keywords"] == ["RDS", "포렌식"]
    next(c for c in at.checkbox if c.label.startswith("내용을 직접 읽고 [확인 필요]")).check().run()
    assert "한글(.hwpx) 내려받기" in [b.proto.label for b in at.get("download_button")] and not errors(at)
