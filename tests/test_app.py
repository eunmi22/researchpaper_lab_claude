"""화면 흐름 시험 (Streamlit AppTest). 외부 호출은 모두 가짜로 바꾼다."""
from streamlit.testing.v1 import AppTest

from core import ai, nanet, pdfio
from tests import sample as S

APP = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app.py")
SUGGEST_FIRST = "이 논문의 연구 방법을 단계별로 쉽게 설명해 줘"
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


def _fake_ai(task, material=""):
    if task == "pages":
        import re
        ns = [int(n) for n in re.findall(r"\[PDF p\.(\d+)\]", material)]
        return {"pages": [{"page": n, "kind": "표지·목차" if n < 10 else "본문", "summary": f"p{n} 요약", "terms": ["용어"],
                           "question": "" if n < 10 else f"질문{n}", "quote": ""} for n in ns]}
    if task == "questions":
        return {"선정 이유": [{"q": "선정 질문", "pages": [19]}], "생각과 의문": [{"q": "의문 질문", "pages": [24]}], "추가 검토점": ["검토 질문"]}
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
    sent, sent_all = {}, []

    def fake(api_key, model, task, material, extra=""):
        sent[task] = material
        sent_all.append((task, material))
        return {"generation_id": "gen_" + task, "task": task, "model": model, "prompt_version": "p1", "data": _fake_ai(task, material), "created": "t", "usage": {}}

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
    # 쪽별 요약: 6쪽씩 나눠 요청하고, 본문 쪽만 카드로 보이며, 메모를 '생각과 의문'으로 모은다
    click(at, "쪽별 요약 만들기")
    assert not errors(at) and len(note["page_notes"]) == 46
    chunks = [len(__import__("re").findall(r"\[PDF p\.\d+\]", m)) for t, m in sent_all if t == "pages"]
    assert max(chunks) <= 6 and sum(chunks) == 46
    memo = next(t for t in at.text_area if t.label == "p.24을 읽고 든 생각")
    assert len([t for t in at.text_area if t.label.endswith("을 읽고 든 생각")]) == 37  # 표지·목차 9쪽은 메모 칸 없음
    memo.set_value("로그 끄는 행위는 CloudTrail에 남나?").run()
    click(at, "쪽별 메모를 ‘생각과 의문’에 모으기")
    th = next(t for t in at.text_area if t.label.startswith("이 논문을 읽고")).value
    assert th.startswith("로그가") and "[p.24] 로그 끄는 행위는 CloudTrail에 남나?" in th and note["my_thoughts"] == th
    click(at, "쪽별 메모를 ‘생각과 의문’에 모으기")
    assert next(t for t in at.text_area if t.label.startswith("이 논문을 읽고")).value.count("[p.24]") == 1  # 두 번 모아도 중복 없음
    # 질문 예시: 쪽별 요약을 근거로 세 칸에 나눠 붙는다
    click(at, "질문 예시 받기")
    assert sent_all[-1][1].startswith("[쪽별 요약]")
    md = " ".join(x.value for x in at.markdown)
    assert "선정 질문" in md and "의문 질문" in md and "검토 질문" in md and note["selection_reason"] == ""
    # 논문에 질문하기
    chat_calls = []

    def fake_chat(api_key, model, material, history):
        chat_calls.append(history)
        return {"text": "표 2는 비교표입니다 [p.19].", "model": model, "created": "t"}

    monkeypatch.setattr(ai, "run_chat", fake_chat)
    next(t for t in at.text_area if t.label == "질문").set_value("표 2를 쉽게 설명해 줘")
    click(at, "질문 보내기")
    assert not errors(at) and chat_calls[0][-1] == {"role": "user", "content": "표 2를 쉽게 설명해 줘"}
    assert [m["role"] for m in note["chat"]] == ["user", "assistant"]
    assert any(":blue-badge[p.19]" in x.value for x in at.markdown)
    click(at, SUGGEST_FIRST)
    assert len(note["chat"]) == 4 and len(chat_calls[1]) == 3
    # 보고서 양식: '간단'으로 바꾸면 세 항목만, 번호를 다시 매긴다
    next(s_ for s_ in at.selectbox if s_.label == "양식").set_value("간단").run()
    heads = [x.value for x in at.markdown if x.value.startswith("**") and ". " in x.value]
    assert "**1. 서지정보**" in heads and "**2. 연구 기여도와 증명방법 (핵심 3줄)**" in heads and "**3. 나의 생각·의문**" in heads
    assert not any(t.label == "연구 개요" for t in at.text_area)
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
