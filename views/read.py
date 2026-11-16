"""02. 논문 읽기 — 원문 준비, 6항목·7문장 요약, 기여도·증명방법, 나의 생각, A4 과제 보고서."""
from __future__ import annotations

import uuid

import streamlit as st

from core import cite, evalq, exporters, nanet, pdfio, store
from views.common import download_doc, goto, need_key_notice, pick_paper, proj, render_blocks, run_ai

READ_STATES = ["읽기 전", "초록만 읽음", "본문 읽는 중", "끝까지 읽음"]


def _register_direct():
    with st.expander("검색 없이 직접 등록 (내려받은 PDF가 있을 때)", expanded=not proj()["papers"]):
        with st.form("direct"):
            c = st.columns(3)
            title = c[0].text_input("제목")
            author = c[1].text_input("저자")
            cat = c[2].selectbox("자료유형", [nanet.THESIS, nanet.ARTICLE])
            c = st.columns(4)
            degree = c[0].selectbox("학위종류 (학위논문)", ["", "석사", "박사"])
            pub = c[1].text_input("학위수여기관 또는 발행처")
            year = c[2].text_input("발행연도")
            month = c[3].text_input("발행월")
            journal = st.text_input("학술지명 (학술지 논문)")
            if st.form_submit_button("등록"):
                if not title.strip():
                    st.error("제목을 입력해 주세요.")
                else:
                    pid = "manual:" + uuid.uuid4().hex[:8]
                    proj()["papers"][pid] = {"paper_id": pid, "source": "사용자 직접 등록", "controlno": "", "title": title.strip(), "author": author.strip(),
                                             "publisher": pub.strip(), "year": year.strip(), "month": month.strip(), "journal": journal.strip(), "doi": "",
                                             "raw_type": "", "category": cat, "degree": degree, "scope": "", "type_basis": "사용자 입력",
                                             "type_conflict": False, "volume": "", "issue": "", "pages": "", "enriched": {}, "user_fix": {}, "raw": {}}
                    proj()["notes"][pid] = store.empty_note()
                    st.session_state.current_pid = pid
                    st.rerun()


def _source(pid: str, note: dict) -> tuple[str, dict]:
    """원문 준비. 반환: (AI에 보낼 본문, 전송 범위 설명)."""
    st.subheader("① 원문 준비")
    up = st.file_uploader("논문 PDF 올리기", type=["pdf"], key=f"pdf_{pid}")
    if up is not None and proj()["docs"].get(pid, {}).get("upload_id") != up.file_id:
        try:
            doc = pdfio.extract(up.getvalue(), up.name)
        except pdfio.PdfError as exc:
            st.error(f"{exc} PDF 대신 아래 ‘초록·발췌문 직접 입력’을 쓸 수 있습니다.")
        else:
            old = proj()["docs"].get(pid, {}).get("document_id")
            st.session_state.texts[pid] = doc["pages"]
            proj()["docs"][pid] = {k: doc[k] for k in ("document_id", "name", "page_count", "empty_pages", "captions", "total_chars", "status")} | {"upload_id": up.file_id}
            if old and old != doc["document_id"]:
                st.warning("다른 PDF로 바뀌었습니다. 이전 PDF로 만든 요약은 아래에 ‘이전 원문 기준’으로 표시됩니다.")
            store.log(proj(), f"원문 등록: {up.name} ({doc['page_count']}쪽)")
    doc, pages = proj()["docs"].get(pid), st.session_state.texts.get(pid)
    if doc and not pages:
        st.warning(f"이 논문에는 ‘{doc['name']}’({doc['page_count']}쪽)이 등록되어 있었지만 본문은 세션에 없습니다. 같은 PDF를 다시 올려 주세요.")
    with st.expander("초록·발췌문 직접 입력 (PDF가 없거나 텍스트가 추출되지 않을 때)", expanded=not pages):
        note["abstract"] = st.text_area("초록 또는 발췌문", note.get("abstract", ""), height=140, key=f"abs_{pid}")
    if pages:
        payload, full, partial = pdfio.build_payload(pages)
        got = doc["page_count"] - len(doc["empty_pages"])
        c = st.columns(4)
        c[0].metric("PDF 전체", f"{doc['page_count']}쪽")
        c[1].metric("텍스트 추출", f"{got}쪽")
        c[2].metric("AI에 전부 전송", f"{len(full)}쪽")
        c[3].metric("AI에 일부만 전송", f"{len(partial)}쪽")
        if doc["status"] == "scanned":
            st.warning("텍스트가 거의 추출되지 않았습니다. 스캔본일 수 있습니다. 이 앱은 글자 인식(OCR)을 하지 않으므로 초록·발췌문을 직접 입력해 주세요.")
        if doc["empty_pages"]:
            st.caption(f"텍스트를 읽지 못한 쪽 (PDF 기준): {doc['empty_pages']} — 그림만 있는 쪽이거나 스캔 쪽입니다.")
        if partial:
            st.caption(f"문서가 길어 다음 쪽은 앞뒤 일부만 보냅니다: {partial}. 이 쪽의 가운데 내용은 요약에 반영되지 않을 수 있습니다.")
        if doc["captions"]:
            st.caption(f"그림·표 {len(doc['captions'])}개는 캡션 글자만 읽습니다. 그림 속 그래프·수치는 분석하지 않습니다.")
        scope = {"kind": "PDF 본문", "document_id": doc["document_id"], "pages_full": full, "pages_partial": partial}
        note["source_scope"] = f"PDF {doc['page_count']}쪽 중 {len(full)}쪽 전부, {len(partial)}쪽 일부 전송"
        return payload, scope
    if note.get("abstract", "").strip():
        st.info("초록·발췌문만으로 분석합니다. 결과에 ‘초록 기반’이라고 표시됩니다.")
        note["source_scope"] = "초록·발췌문 기반 (본문 미확인)"
        return "[초록·발췌문]\n" + note["abstract"], {"kind": "초록·발췌문"}
    st.warning("PDF 또는 초록이 필요합니다. 제목만으로는 본문을 분석하지 않습니다.")
    note["source_scope"] = ""
    return "", {}


def _items_table(pid: str, data: dict, key: str):
    items = data.get("data", {}).get("items") or data.get("data", {}).get("sections") or {}
    pages = st.session_state.texts.get(pid)
    rows = []
    for name, item in items.items():
        item = item or {}
        pg = item.get("pages") or []
        rows.append({"항목": name, "내용": item.get("text", ""), "근거 쪽 (PDF 기준)": ", ".join(f"p.{p}" for p in pg) or "없음",
                     "근거 문구": item.get("quote", ""),
                     "원문 대조": pdfio.verify_quote(pages, pg[0] if pg else None, item.get("quote", "")) if pages else "원문 없음"})
    st.dataframe(rows, width="stretch", hide_index=True, key=key)
    ctx = data.get("context", {})
    cur = proj()["docs"].get(pid, {}).get("document_id")
    stale = " · ⚠ 이전 원문 기준" if ctx.get("document_id") and cur and ctx["document_id"] != cur else ""
    st.caption(f"{ctx.get('kind', '')} 기준 · 모델 {data.get('model')} · {data.get('created')}{stale} · "
               "‘원문 대조’는 근거 문구가 추출 텍스트에 실제로 있는지만 확인합니다. 쪽 번호는 PDF 쪽이며 논문에 인쇄된 쪽과 다를 수 있습니다.")


def _ai_section(pid: str, note: dict, material: str, scope: dict):
    st.subheader("② AI 사전 요약 (직접 읽기 전 길잡이)")
    ready = need_key_notice() and bool(material)
    tabs = st.tabs(["과제용 6항목", "읽기용 7문장", "기여도·증명방법·한계"])
    for tab, task, label in ((tabs[0], "summary6", "6항목 요약 만들기"), (tabs[1], "summary7", "7문장 요약 만들기")):
        with tab:
            if st.button(label, key=f"{task}_{pid}", disabled=not ready):
                gen = run_ai(task, material, pid=pid, context=scope)
                if gen:
                    note[task] = gen
            if note.get(task):
                _items_table(pid, note[task], f"tbl_{task}_{pid}")
                if st.button("이 요약 품질 확인", key=f"q_{task}_{pid}", help="형식과 근거 문구를 규칙으로 검사합니다. AI를 다시 부르지 않습니다."):
                    case = evalq.new_case(task_type=task, response=note[task]["data"], provided_contexts=[material])
                    res = evalq.rule_checks(case, st.session_state.texts.get(pid))
                    st.dataframe([{"검사": k, "판정": v["state"], "설명": v["detail"]} for k, v in res.items()], hide_index=True, width="stretch")
                    st.caption("규칙 검사는 형식과 문구 일치만 봅니다. 내용이 논문의 뜻과 맞는지는 직접 읽고 확인해 주세요.")
    with tabs[2]:
        if st.button("기여도·증명방법 분석하기", key=f"method_{pid}", disabled=not ready):
            gen = run_ai("method", material, pid=pid, context=scope)
            if gen:
                note["method"] = gen["data"] | {"_gen": {k: gen[k] for k in ("model", "created", "context")}}
        m = note.get("method")
        if m:
            _items_table(pid, {"data": m, **m.get("_gen", {})}, f"tbl_m_{pid}")
            st.markdown("**기여도·증명방법 3줄 (AI 초안)**")
            for k in exporters.THREE:
                st.write(f"- **{k}** — {(m.get('three_lines') or {}).get(k, '')}")
            with st.expander("따라 해 보기 검토 (재현에 필요한 것)"):
                st.dataframe([{"항목": k, "내용": v} for k, v in (m.get("replication") or {}).items()], hide_index=True, width="stretch")
                st.caption("‘검토 가능’과 ‘실제로 재현에 성공함’은 다릅니다. 직접 해 본 결과만 보고서에 쓰세요.")


def _thoughts(pid: str, note: dict, material: str, scope: dict):
    st.subheader("③ 나의 생각·의문 (직접 작성)")
    note["read_state"] = st.select_slider("읽기 상태", READ_STATES, value=note.get("read_state", READ_STATES[0]), key=f"rs_{pid}")
    note["my_thoughts"] = st.text_area("이 논문을 읽고 든 생각과 의문", note.get("my_thoughts", ""), height=130, key=f"th_{pid}",
                                       help="AI가 이 칸을 채우거나 고치지 않습니다.")
    c1, c2 = st.columns([1, 3])
    if c1.button("질문 예시 받기", key=f"qs_{pid}", disabled=not (material and st.session_state.get("openai_key"))):
        gen = run_ai("questions", material, pid=pid, context=scope)
        if gen:
            note["ai_questions"] = gen["data"].get("questions", [])
    if note.get("ai_questions"):
        with c2.container(border=True):
            st.caption("AI가 제안한 질문 예시입니다. 참고만 하고, 본인 생각은 위 칸에 직접 적어 주세요.")
            for q in note["ai_questions"]:
                st.write("· " + str(q))


def _report(pid: str, note: dict):
    st.subheader("④ A4 과제 요약 보고서")
    f = exporters.report_fields(proj(), pid)
    labels = {"overview": "연구 개요", "tech": "활용 기술·분석 대상", "contribution": "기여 (1문장)", "proof": "증명방법 (1문장)",
              "scope": "검증 범위·한계 (1문장)", "limits": "논문에 명시된 한계"}
    with st.form(f"report_{pid}"):
        st.caption("AI 초안을 고쳐 쓰세요. 고친 내용은 AI 초안과 따로 보관합니다.")
        note_reason = st.text_area("선정 이유 (직접 작성)", f["reason"], height=70)
        vals = {k: st.text_area(lab, f[k], height=70) for k, lab in labels.items()}
        extra = st.text_area("내가 추가로 검토할 점 (직접 작성)", f["extra_limits"], height=70)
        replicated = st.checkbox("이 논문의 방법을 직접 따라 해 본 결과를 위 내용에 적었습니다", value=note.get("replicated", False))
        if st.form_submit_button("보고서 내용 저장", type="primary"):
            note["selection_reason"], note["extra_limits"], note["replicated"] = note_reason, extra, replicated
            note["edited"] = {k: v for k, v in vals.items() if v.strip() != f["_ai"][k].strip()}
            store.log(proj(), "보고서 내용 저장")
            st.rerun()
    edited = [labels[k] for k in note.get("edited", {})]
    st.caption("AI 초안 그대로인 칸: " + (", ".join(v for k, v in labels.items() if k not in note.get("edited", {}) and f[k]) or "없음")
               + " · 내가 고친 칸: " + (", ".join(edited) or "없음"))
    blocks = exporters.report_blocks(proj(), pid, note.get("replicated", False))
    n = exporters.char_count(blocks)
    if n > 1800:
        st.warning(f"현재 약 {n:,}자입니다. A4 한 쪽은 대략 1,600~1,800자여서 넘칠 수 있습니다. 글씨를 줄이지 말고 내용을 줄이거나 두 쪽으로 제출하세요.")
    else:
        st.caption(f"현재 약 {n:,}자 (A4 한 쪽 기준 대략 1,600~1,800자). 실제 쪽수는 내려받은 파일에서 확인해 주세요.")
    with st.container(border=True):
        render_blocks(blocks)
    missing = cite.missing_fields(proj()["papers"][pid])
    if missing:
        st.warning(f"참고문헌에 확인되지 않은 값이 있습니다: {missing}. 01 메뉴의 ‘서지정보 확인·교정’ 또는 아래에서 채워 주세요.")
        with st.expander("서지정보 바로 고치기"):
            rec = proj()["papers"][pid]
            eff = cite.effective(rec)
            with st.form(f"rfix_{pid}"):
                c = st.columns(3)
                vals2 = {k: c[i % 3].text_input(cite.FIELD_LABELS[k], eff.get(k, "")) for i, k in enumerate(cite.FIX_FIELDS)}
                if st.form_submit_button("저장"):
                    base = cite.effective({**rec, "user_fix": {}})
                    rec["user_fix"] = {**{k: v for k, v in rec["user_fix"].items() if k == "category"},
                                       **{k: v.strip() for k, v in vals2.items() if v.strip() != str(base.get(k, "")).strip()}}
                    st.rerun()
    if st.checkbox("내용을 직접 읽고 확인했습니다 (제출 전 최종 검토)", key=f"final_{pid}"):
        download_doc(blocks, "논문요약보고서", f"rep_{pid}", "논문 요약 보고서")
    else:
        st.caption("최종 검토에 체크하면 Word·한글 파일을 내려받을 수 있습니다.")


def render():
    st.header("02. 논문 읽기")
    _register_direct()
    if not proj()["papers"]:
        st.info("01. 자료 찾기에서 논문을 고르거나, 위에서 직접 등록해 주세요.")
        return
    pid = pick_paper("read_pick")
    note = proj()["notes"].setdefault(pid, store.empty_note())
    st.code(cite.format_citation(proj()["papers"][pid]), language=None, wrap_lines=True)
    material, scope = _source(pid, note)
    _ai_section(pid, note, material, scope)
    _thoughts(pid, note, material, scope)
    _report(pid, note)
    st.divider()
    if st.button("03. 선행연구 분석으로 보내기", help="서지정보, 요약, 근거, 나의 생각을 함께 넘깁니다."):
        note["in_lit"] = True
        goto("03. 선행연구 분석")
