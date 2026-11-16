"""01. 자료 찾기 — 국회도서관 검색 / 대학 학위논문 / 기타 자료·통계."""
from __future__ import annotations

from urllib.parse import quote_plus

import streamlit as st

from core import cite, nanet, store
from views.common import goto, proj, secret, steps

UNIV = {"세종사이버대학교": "https://sjcu.dcollection.net", 
        "서울대학교": "https://dcollection.snu.ac.kr", "연세대학교": "https://dcollection.yonsei.ac.kr",
        "성균관대학교": "https://dcollection.skku.ac.kr", "전국 대학 dCollection 목록": "https://www.dcollection.net/common/instList.do", "RISS 학술연구정보서비스": "https://www.riss.kr"}


def _do_search(keyword, pages, per_page, field, enrich):
    key = secret("DATA_GO_KR_API_KEY")
    try:
        run = nanet.run_search(key, keyword, pages, per_page, field)
    except nanet.NanetError as exc:
        st.session_state.search_error = {"kind": exc.kind, "msg": str(exc), "keyword": keyword}
        return
    st.session_state.search_error = None
    st.session_state.run = run
    st.session_state.pop("pick_id", None)
    if enrich:
        _enrich(run)
    store.log(proj(), f"검색: '{run.keyword}' ({run.field}) {len(run.records)}건 수집")


def _enrich(run, limit: int = 15):
    todo = [r for r in run.records if r["category"] == nanet.THESIS and not r["degree"] and not r["enriched"]][:limit]
    if not todo:
        return
    bar = st.progress(0.0, "공식 상세정보에서 학위종류·수여연월을 확인하는 중")
    for i, r in enumerate(todo, start=1):
        r["enriched"] = nanet.fetch_detail(r["controlno"]) or {"_checked_at": "확인 실패"}
        bar.progress(i / len(todo))
    bar.empty()


def _more(pages: int, start: int | None = None):
    run = st.session_state.run
    before = len(run.records)
    try:
        nanet.run_search(secret("DATA_GO_KR_API_KEY"), run.keyword, pages, run.per_page, run.field, run=run, start_page=start)
    except nanet.NanetError as exc:
        st.session_state.search_error = {"kind": exc.kind, "msg": str(exc), "keyword": run.keyword}
        return
    st.toast(f"{len(run.records) - before}건을 더 가져왔습니다.")


def _fix_form(rec: dict):
    with st.expander("서지정보 확인·교정", expanded=bool(cite.missing_fields(rec))):
        st.caption("값마다 출처를 표시합니다. 고친 값은 ‘사용자 교정’으로 따로 보관하고 원래 값은 지우지 않습니다. 모르는 값은 비워 두면 ‘미확인’으로 표시됩니다.")
        eff = cite.effective(rec)
        with st.form(f"fix_{rec['paper_id']}"):
            cols = st.columns(3)
            vals = {}
            for i, k in enumerate(cite.FIX_FIELDS):
                vals[k] = cols[i % 3].text_input(f"{cite.FIELD_LABELS[k]} · {cite.provenance(rec, k)}", eff.get(k, ""), key=f"fx_{rec['paper_id']}_{k}")
            cats = nanet.CATEGORY_ORDER
            vals["category"] = st.selectbox("자료 대분류", cats, index=cats.index(eff.get("category", nanet.UNKNOWN)))
            if st.form_submit_button("교정값 저장"):
                base = cite.effective({**rec, "user_fix": {}})
                rec["user_fix"] = {k: v.strip() for k, v in vals.items() if v.strip() != str(base.get(k, "")).strip()}
                if rec["paper_id"] in proj()["papers"]:
                    proj()["papers"][rec["paper_id"]] = rec
                st.rerun()
        if rec.get("enriched", {}).get("_source"):
            st.caption(f"공식 상세 보완 출처: {rec['enriched']['_source']} · 확인 {rec['enriched'].get('_checked_at', '')}")


def _selection(run):
    recs = nanet.sort_records(run.records)
    counts = {c: sum(1 for r in recs if cite.effective(r)["category"] == c) for c in nanet.CATEGORY_ORDER}
    st.subheader("읽을 논문·자료를 선택하세요")
    m = st.columns(6)
    m[0].metric("API 전체 건수", f"{run.total:,}")
    m[1].metric("수집·중복 제거", f"{len(recs):,}")
    for col, c in zip(m[2:], nanet.CATEGORY_ORDER):
        col.metric(c.replace("자료유형 ", ""), counts[c])
    if len(recs) < run.total:
        st.caption(f"전체 {run.total:,}건 중 {len(recs):,}건만 가져온 상태입니다 (수집한 페이지: {run.pages_ok}). 유형별 건수는 가져온 자료 기준입니다.")
    with st.container(border=True):
        c1, c2 = st.columns([1, 3])
        avail = [c for c in nanet.CATEGORY_ORDER if counts[c]]
        cat = c1.selectbox("① 대분류", avail, format_func=lambda c: f"{c} ({counts[c]})", key="pick_cat",
                           help="가져온 자료를 좁혀 보는 기능입니다. 검색 조건을 바꾸지 않습니다.")
        ids = [r["paper_id"] for r in recs if cite.effective(r)["category"] == cat]
        by_id = {r["paper_id"]: r for r in recs}

        def label(pid):
            e = cite.effective(by_id[pid])
            return f"{e['title'] or '[제목 미확인]'} | {e['author'] or '저자 미확인'} | {e['year'] or '연도 미확인'} | {e['controlno'] or pid}"

        if st.session_state.get("pick_id") not in ids:
            st.session_state.pick_id = ids[0]
        pid = c2.selectbox("② 제목 | 저자 | 연도 | 관리번호", ids, format_func=label, key="pick_id")
        rec = by_id[pid]
        eff = cite.effective(rec)
        st.markdown(f"#### {eff['title'] or '[제목 미확인]'}")
        degree = f" · {eff['degree']}" if eff.get("degree") else ""
        st.caption(f"{eff['category']}{degree} · 분류 근거: {rec['type_basis']} · 관리번호 {eff['controlno'] or '없음(임시 식별자)'}")
        st.write(f"저자 {eff['author'] or '미확인'} · 발행 {eff['publisher'] or '미확인'} · {eff['year'] or '연도 미확인'}"
                 + (f" · 수록 {eff['journal']}" if eff.get("journal") else ""))
        st.markdown("**정식 참고문헌**")
        st.code(cite.format_citation(rec), language=None, wrap_lines=True)
        _fix_form(rec)
        b1, b2, b3 = st.columns(3)
        url = nanet.detail_url(eff["controlno"])
        if url:
            b1.link_button("공식 상세정보 열기", url, width="stretch", help="국회도서관 상세페이지입니다. 원문 PDF 다운로드 버튼이 아닙니다.")
        if eff["category"] == nanet.THESIS and not eff.get("degree") and b2.button("학위종류·수여연월 확인", width="stretch",
                                                                                help="공식 상세페이지에서 이 자료 한 건의 학위 정보를 읽어 옵니다."):
            rec["enriched"] = nanet.fetch_detail(eff["controlno"])
            if not rec["enriched"]:
                st.warning("상세페이지에서 학위 정보를 읽지 못했습니다. ‘서지정보 확인·교정’에서 직접 입력해 주세요.")
            else:
                st.rerun()
        blocked = {nanet.BOOK: "선택한 자료는 도서입니다. 상세정보는 볼 수 있지만 논문 읽기로 넘기지 않습니다.",
                   nanet.UNKNOWN: "자료유형이 확인되지 않았습니다. ‘서지정보 확인·교정’에서 대분류를 확인한 뒤 진행해 주세요."}.get(eff["category"])
        if blocked:
            st.info(blocked)
        if b3.button("02. 논문 읽기 시작", type="primary", width="stretch", disabled=bool(blocked)):
            proj()["papers"][pid] = rec
            proj()["notes"].setdefault(pid, store.empty_note())
            st.session_state.current_pid = pid
            if not proj()["project"]["topic"]:
                proj()["project"]["topic"] = run.keyword
            goto("02. 논문 읽기")
    return recs


def _nanet_tab():
    steps([("주제 검색", "검색어를 입력하고 검색 실행"), ("대분류 선택", "학위논문 · 학술기사 · 도서자료"),
           ("자료 선택·확인", "콤보박스에서 한 편 선택"), ("논문 읽기", "02로 이동해 PDF·초록 준비")],
          current=2 if st.session_state.run else 1)
    has_key = bool(secret("DATA_GO_KR_API_KEY"))
    if not has_key:
        st.warning("관리자 공공데이터 API 설정이 필요합니다 (Secrets의 DATA_GO_KR_API_KEY). 개인 OpenAI 키와는 다른 키입니다.")
    with st.form("search_form"):
        keyword = st.text_input("검색할 논문 주제", placeholder="데이터베이스 포렌식")
        c1, c2 = st.columns(2)
        pages = c1.selectbox("검색 페이지 수", [1, 2, 3, 4, 5], index=1)
        per_page = c2.selectbox("페이지당 검색 건수", [10, 20, 50], index=1)
        with st.expander("검색 방식·서지정보 보완 설정"):
            field = st.selectbox("검색 대상", nanet.SEARCH_FIELDS, help="공식 명세의 검색항목입니다. 입력한 검색어는 줄이거나 늘리지 않고 그대로 보냅니다.")
            enrich = st.checkbox("학위논문의 학위종류·수여연월을 공식 상세페이지에서 자동 확인 (최대 15건, 느려질 수 있음)", value=False)
        go = st.form_submit_button("논문 검색 실행", type="primary", width="stretch", disabled=not has_key)
    if go:
        if not keyword.strip():
            st.error("검색어를 입력해 주세요.")
        else:
            with st.spinner("국회도서관에서 검색하는 중"):
                _do_search(keyword, pages, per_page, field, enrich)
    err, run = st.session_state.get("search_error"), st.session_state.run
    if err:
        kinds = {"config": "설정 문제", "http": "연결 문제", "api": "API 업무 오류", "parse": "응답 해석 오류"}
        st.error(f"‘{err['keyword']}’ 검색이 실패했습니다 ({kinds.get(err['kind'], '오류')}): {err['msg']} 검색 결과가 0건이라는 뜻이 아닙니다.")
        if run:
            st.caption(f"아래는 이전에 성공한 검색 ‘{run.keyword}’의 결과입니다.")
    if not run:
        if not err:
            st.info("검색어를 입력하고 ‘논문 검색 실행’을 눌러 주세요.")
        return
    st.markdown(f"**검색어 ‘{run.keyword}’ · 검색 대상 {run.field}**")
    if run.total == 0 or not run.records:
        st.info("국회도서관 검색 결과가 0건입니다. ‘대학 학위논문 검색’ 탭에서 대학 저장소를 찾아보세요.")
        return
    recs = _selection(run)
    c1, c2 = st.columns(2)
    if run.failed_page:
        st.warning(f"{run.failed_page}페이지를 가져오지 못했습니다. 앞에서 가져온 결과는 그대로 있습니다.")
        if c1.button(f"{run.failed_page}페이지 다시 시도"):
            _more(1, run.failed_page)
            st.rerun()
    elif len(recs) < run.total and c1.button("같은 조건으로 다음 페이지 더 가져오기"):
        _more(1)
        st.rerun()
    with st.expander(f"전체 검색 결과 목록 펼치기 ({len(recs)}건)"):
        st.dataframe([{"대분류": cite.effective(r)["category"], "제목": cite.effective(r)["title"], "저자": cite.effective(r)["author"],
                       "연도": cite.effective(r)["year"], "관리번호": r["controlno"], "정식 참고문헌": cite.format_citation(r)} for r in recs],
                     width="stretch", hide_index=True)
    unknown = [r for r in recs if cite.effective(r)["category"] == nanet.UNKNOWN]
    with st.expander(f"미확인 목록 ({len(unknown)}건)"):
        q = st.text_input("목록 안에서 찾기", key="unk_q")
        rows = [{"관리번호": r["controlno"] or r["paper_id"], "제목": r["title"], "저자": r["author"], "연도": r["year"], "발행처": r["publisher"],
                 "미확인 이유": r["type_basis"], "공식 링크": nanet.detail_url(r["controlno"])} for r in unknown]
        rows = [x for x in rows if not q or q.lower() in " ".join(map(str, x.values())).lower()]
        st.dataframe(rows, width="stretch", hide_index=True, column_config={"공식 링크": st.column_config.LinkColumn()})
    with st.expander("검색 진단"):
        st.caption(f"요청 주소 /9720000/searchservice/basic · 중복으로 제외 {run.duplicates}건 · 인증키는 기록하지 않습니다. "
                   "이 순서는 API가 돌려준 순서를 유형별로 다시 묶은 것이며 공식 웹의 관련도 순서와 같다고 보장하지 않습니다.")
        st.dataframe(run.diagnostics, width="stretch", hide_index=True)


def _univ_tab():
    st.caption("대학 저장소는 이 앱이 직접 수집하지 않습니다. 아래 버튼은 새 창에서 해당 사이트를 엽니다.")
    kw = st.text_input("검색어", key="univ_kw", placeholder="데이터베이스 포렌식")
    st.markdown("**검색어가 반영되는 버튼**")
    if kw.strip():
        st.link_button("Google Scholar에서 dCollection 학위논문 찾기", "https://scholar.google.com/scholar?q=" + quote_plus(f"{kw} site:dcollection.net"))
    else:
        st.caption("검색어를 입력하면 버튼이 나타납니다.")
    st.markdown("**사이트 첫 화면만 여는 버튼** (열린 뒤 그 사이트에서 검색어를 직접 입력)")
    cols = st.columns(3)
    for i, (name, url) in enumerate(UNIV.items()):
        cols[i % 3].link_button(name, url, width="stretch")
    st.caption("원문을 내려받았다면 02. 논문 읽기에서 ‘검색 없이 직접 등록’으로 PDF를 올릴 수 있습니다.")


def _etc_tab():
    run = st.session_state.run
    if run and run.records:
        st.markdown(f"**‘{run.keyword}’로 가져온 자료 {len(run.records)}건의 발행연도 분포** (전체 검색 결과가 아니라 가져온 자료 기준)")
        years = {}
        for r in run.records:
            if r["year"]:
                years[r["year"]] = years.get(r["year"], 0) + 1
        if years:
            st.bar_chart({"건수": dict(sorted(years.items()))}, x_label="발행연도", y_label="건수")
    else:
        st.info("국회도서관 검색을 먼저 실행하면 가져온 자료의 연도 분포를 볼 수 있습니다.")
    st.markdown("**기타 자료 사이트** (첫 화면만 엽니다)")
    cols = st.columns(3)
    for i, (name, url) in enumerate({"KCI 한국학술지인용색인": "https://www.kci.go.kr", "국가통계포털 KOSIS": "https://kosis.kr"}.items()):
        cols[i].link_button(name, url, width="stretch")


def render():
    st.header("01. 자료 찾기")
    t1, t2, t3 = st.tabs(["국회도서관 논문 검색", "대학 학위논문 검색", "기타 자료·통계"])
    with t1:
        _nanet_tab()
    with t2:
        _univ_tab()
    with t3:
        _etc_tab()
