"""화면 흐름 시험 (Streamlit AppTest). 외부 호출은 모두 가짜로 바꾼다."""
import pytest
from streamlit.testing.v1 import AppTest

from core import ai, nanet, pdfio
from tests import sample as S

APP = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app.py")
PDF = "/mnt/user-data/uploads/167e4573-Amazon_RDS_______.pdf"


def app(monkeypatch, key="TESTKEY-not-real-0123456789"):
    monkeypatch.setenv("DATA_GO_KR_API_KEY", key)
    at = AppTest.from_file(APP, default_timeout=30)
    return at.run()


def errors(at):
    return [e.value for e in at.exception] + [e.value for e in at.error]


def click(at, label):
    btn = next(b for b in at.button if b.label == label)
    return btn.click().run()


def search(at, monkeypatch, pages):
    sess = S.FakeSession(pages)
    monkeypatch.setattr(nanet.requests, "get", sess.get)
    next(t for t in at.text_input if t.label == "검색할 논문 주제").set_value("데이터베이스 포렌식")
    click(at, "논문 검색 실행")
    return sess


def test_all_menus_render_without_key(monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_API_KEY", raising=False)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("관리자 공공데이터 API 설정" in w.value for w in at.warning)
    for menu in at.sidebar.radio[0].options:
        at.sidebar.radio[0].set_value(menu).run()
        assert not at.exception, menu
    assert any("잠겨 있습니다" in w.value for w in at.warning)  # 관리자 비밀번호 미설정 → 잠금


def test_search_select_and_handoff(monkeypatch):
    at = app(monkeypatch)
    sess = search(at, monkeypatch, {1: S.response(6, [S.BOOK, S.ARTICLE, S.THESIS]), 2: S.response(6, [S.CONFLICT, S.NOTYPE, S.NOID])})
    assert not errors(at) and sess.calls[0]["search"] == "전체,데이터베이스 포렌식"
    m = {x.label: x.value for x in at.metric}
    assert m["API 전체 건수"] == "6" and m["수집·중복 제거"] == "6" and (m["학위논문"], m["학술기사"], m["도서자료"], m["미확인"]) == ("1", "2", "1", "2")
    cat = next(s for s in at.selectbox if s.label.startswith("①"))
    assert cat.value == nanet.THESIS  # 학위논문 우선
    assert any("[학위종류 미확인]" in c.value for c in at.code)
    # AT-11: 도서는 읽기로 넘기지 않는다
    cat.set_value(nanet.BOOK).run()
    assert next(b for b in at.button if b.label == "02. 논문 읽기 시작").disabled
    # AT-09: 대분류를 바꾸면 그 유형 자료만 선택된다
    next(s for s in at.selectbox if s.label.startswith("①")).set_value(nanet.ARTICLE).run()
    pick = next(s for s in at.selectbox if s.label.startswith("②"))
    assert pick.value in ("nanet:KINX2026000002",) or pick.value.startswith("tmp:")
    next(s for s in at.selectbox if s.label.startswith("①")).set_value(nanet.THESIS).run()
    click(at, "02. 논문 읽기 시작")
    assert not errors(at) and at.session_state.menu == "02. 논문 읽기"
    assert at.session_state.current_pid == "nanet:KDMT1202600000001"  # AT-13: 같은 ID로 전달
    assert any("제목만으로는 본문을 분석하지 않습니다" in w.value for w in at.warning)  # AT-14


def test_failed_new_search_keeps_old_result_marked(monkeypatch):
    at = app(monkeypatch)
    search(at, monkeypatch, {1: S.response(1, [S.THESIS])})
    search(at, monkeypatch, {1: S.response(0, [], code="22")})
    assert any("검색이 실패했습니다" in e.value and "0건이라는 뜻이 아닙니다" in e.value for e in at.error)
    assert any("이전에 성공한 검색" in c.value for c in at.caption)


def test_zero_results_is_not_error(monkeypatch):
    at = app(monkeypatch)
    search(at, monkeypatch, {1: S.response(0, [])})
    assert not at.error and any("0건입니다" in i.value for i in at.info)


def _fake_ai(task):
    item = {"text": "클라우드 DB 포렌식 절차를 제안한다.", "pages": [5], "quote": "대규모 클라우드 DB에 대한 디지털포렌식 기법은 아직 정립되지 않았다"}
    return {"summary6": {"items": {k: dict(item) for k in ai.SIX}},
            "method": {"sections": {"한계": {"text": "삭제 레코드 복구는 할 수 없다.", "pages": [1], "quote": "원문에 없는 지어낸 근거 문구입니다"}},
                       "three_lines": {"기여": "절차를 제안했다.", "증명방법": "단계별 비교와 사례 구체화.", "검증 범위": "자체 실험 수치는 확인되지 않음."},
                       "replication": {"필요 데이터": "스냅숏"}}}[task]


def test_read_report_flow_with_real_pdf(monkeypatch):
    at = app(monkeypatch)
    search(at, monkeypatch, {1: S.response(1, [S.THESIS])})
    click(at, "02. 논문 읽기 시작")
    pid = at.session_state.current_pid
    doc = pdfio.extract(open(PDF, "rb").read(), "rds.pdf")
    at.session_state.texts[pid] = doc["pages"]
    at.session_state.proj["docs"][pid] = {k: doc[k] for k in ("document_id", "name", "page_count", "empty_pages", "captions", "total_chars", "status")}
    at.run()
    assert not errors(at)
    m = {x.label: x.value for x in at.metric}
    assert m["PDF 전체"] == "46쪽" and m["AI에 전부 전송"] == "46쪽"
    # 키가 없으면 AI 버튼은 꺼져 있고 안내가 나온다
    assert next(b for b in at.button if b.label == "6항목 요약 만들기").disabled
    sent = {}

    def fake(api_key, model, task, material, extra=""):
        sent[task] = material
        return {"generation_id": "gen_" + task, "task": task, "model": model, "prompt_version": "p1", "data": _fake_ai(task), "created": "t", "usage": {}}

    monkeypatch.setattr(ai, "run_task", fake)
    at.text_input(key="openai_key").set_value("sk-test-key-for-ui").run()
    click(at, "6항목 요약 만들기")
    click(at, "기여도·증명방법 분석하기")
    assert not errors(at) and "[PDF p.40]" in sent["summary6"] and "5. 결론" in sent["summary6"]  # AT-15: 뒷부분 결론까지 전송
    tables = [d.value for d in at.dataframe]
    s6 = next(t for t in tables if "원문 대조" in t.columns and len(t) == 6)
    assert set(s6["원문 대조"]) == {"일치"}
    meth = next(t for t in tables if "원문 대조" in t.columns and len(t) == 1)
    assert meth["원문 대조"].iloc[0] == "불일치"  # 지어낸 근거 문구는 걸러 표시
    next(t for t in at.text_area if t.label.startswith("이 논문을 읽고")).set_value("로그가 꺼져 있었다면 누가 했는지 어떻게 밝히나?").run()
    note = at.session_state.proj["notes"][pid]
    assert note["my_thoughts"].startswith("로그가") and "sk-test" not in str(at.session_state.proj)
    md = " ".join(x.value for x in at.markdown)
    assert "절차를 제안했다." in md and "단계별 비교와 사례 구체화." in md
    assert not [b for b in at.get("download_button") if "docx" in str(b.proto.label).lower() or "Word" in b.proto.label]  # 최종 검토 전에는 내려받기 없음
    next(c for c in at.checkbox if c.label.startswith("내용을 직접 읽고")).check().run()
    labels = [b.proto.label for b in at.get("download_button")]
    assert "Word(.docx) 내려받기" in labels and "한글(.hwpx) 내려받기" in labels and not errors(at)
    # 03으로 넘기고 나머지 메뉴도 자료가 있는 상태에서 열린다
    click(at, "03. 선행연구 분석으로 보내기")
    assert at.session_state.menu.startswith("03") and any("단일 연구 분석" in i.value for i in at.info)
    for menu in at.sidebar.radio[0].options:
        at.sidebar.radio[0].set_value(menu).run()
        assert not errors(at), menu


def test_admin_gate_and_rule_only_run(monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "open-sesame")
    at = app(monkeypatch)
    at.sidebar.radio[0].set_value("관리자: AI 품질평가").run()
    assert not at.tabs
    next(t for t in at.text_input if "비밀번호" in t.label).set_value("wrong")
    click(at, "들어가기")
    assert any("맞지 않습니다" in e.value for e in at.error) and not at.tabs
    next(t for t in at.text_input if "비밀번호" in t.label).set_value("open-sesame")
    click(at, "들어가기")
    assert at.session_state.role == "관리자" and len(at.tabs) == 6
    from core import evalq
    at.session_state.proj["eval"]["cases"].append(evalq.new_case(task_type="summary6", response={"items": {}}, provided_contexts=["근거"]))
    at.run()
    click(at, "평가 실행")
    run = at.session_state.proj["eval"]["runs"][-1]
    cid = run["conditions"]["case_ids"][0]
    assert run["state"] == "완료" and run["rules"][cid]["형식 준수"]["state"] == "기준 미달" and not errors(at)
