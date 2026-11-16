"""02. 논문 읽기 — 원문 준비, 6항목·7문장 요약, 기여도·증명방법, 나의 생각, A4 과제 보고서."""
from __future__ import annotations

import uuid

import streamlit as st

import re

from core import ai, cite, evalq, exporters, nanet, pdfio, reporttpl, store
from views.common import download_doc, goto, need_key_notice, pick_paper, proj, render_blocks, run_ai

READ_STATES = ["읽기 전", "초록만 읽음", "본문 읽는 중", "끝까지 읽음"]
PAGE_KINDS = ["본문", "표지·목차", "참고문헌·부록"]
SUGGEST = ["이 논문의 연구 방법을 단계별로 쉽게 설명해 줘", "표와 그림 중 핵심인 것을 골라 무엇을 보여 주는지 알려 줘", "저자가 밝힌 한계와 후속 연구 방향을 정리해 줘"]
MY_FIELDS = [("reason", "selection_reason", "선정 이유", "선정 이유", 80),
             ("th", "my_thoughts", "이 논문을 읽고 든 생각과 의문", "생각과 의문", 130),
             ("extra", "extra_limits", "내가 추가로 검토할 점", "추가 검토점", 80)]


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


def _pages_run(pid: str, note: dict, pages: list[str], scope: dict) -> None:
    """빈 쪽은 로컬에서 표시하고, 남은 쪽을 6쪽씩 나눠 요청한다. 실패하면 거기서 멈추고 앞 결과는 남긴다."""
    notes = note.setdefault("page_notes", {})
    for i, t in enumerate(pages, start=1):
        if str(i) not in notes and len(t.strip()) < 20:
            notes[str(i)] = {"kind": "글자 없음", "summary": "추출된 글자가 없는 쪽입니다 (그림만 있거나 스캔한 쪽).", "terms": [], "question": "", "quote": ""}
    todo = [i for i in range(1, len(pages) + 1) if str(i) not in notes]
    chunks = [todo[i:i + 6] for i in range(0, len(todo), 6)]
    bar = st.progress(0.0, "쪽별 요약을 시작합니다")
    for k, chunk in enumerate(chunks, start=1):
        bar.progress((k - 1) / len(chunks), f"쪽별 요약 중… p.{chunk[0]}~{chunk[-1]} ({len(notes)}/{len(pages)}쪽)")
        text = "\n\n".join(f"[PDF p.{n}]\n{pages[n - 1][:3500]}" for n in chunk)
        gen = run_ai("pages", text, pid=pid, context=scope | {"pages_requested": chunk})
        if not gen:
            st.warning("여기서 멈췄습니다. ‘남은 쪽 이어서 요약’을 누르면 남은 쪽부터 다시 요청합니다.")
            break
        for it in gen["data"].get("pages") or []:
            try:
                n = int(it.get("page"))
            except (TypeError, ValueError):
                continue
            if n in chunk:
                notes[str(n)] = {"kind": it.get("kind") if it.get("kind") in PAGE_KINDS else "본문", "summary": str(it.get("summary") or ""),
                                 "terms": [str(x) for x in (it.get("terms") or [])][:3], "question": str(it.get("question") or ""), "quote": str(it.get("quote") or "")}
    bar.empty()
    left = len(pages) - len(notes)
    if left:
        st.info(f"{left}쪽은 아직 요약되지 않았습니다. ‘남은 쪽 이어서 요약’을 누르면 그 쪽만 다시 요청합니다.")
    else:
        st.success("모든 쪽을 요약했습니다. 쪽마다 떠오른 생각을 적어 보세요.")


def _gather_memos(pid: str, note: dict) -> int:
    memos = note.setdefault("memos", {})
    for n in list(note.get("page_notes", {})):
        key = f"memo_{pid}_{n}"
        if key in st.session_state:
            memos[n] = st.session_state[key]
    lines = [f"[p.{n}] {str(v).strip()}" for n, v in sorted(memos.items(), key=lambda x: int(x[0])) if str(v).strip()]
    cur = st.session_state.get(f"th_{pid}", note.get("my_thoughts", ""))
    add = [line for line in lines if line not in cur]
    if add:
        new = (cur.rstrip() + "\n" if cur.strip() else "") + "\n".join(add)
        note["my_thoughts"] = st.session_state[f"th_{pid}"] = new
    return len(add)


def _pages_tab(pid: str, note: dict, scope: dict) -> None:
    pages = st.session_state.texts.get(pid)
    notes = note.setdefault("page_notes", {})
    memos = note.setdefault("memos", {})
    if not pages:
        st.info("쪽별 요약은 PDF 본문이 있어야 합니다. ① 원문 준비에서 PDF를 올려 주세요.")
        if not notes:
            return
    else:
        c1, c2 = st.columns([1, 2])
        label = "남은 쪽 이어서 요약" if notes else "쪽별 요약 만들기"
        done_all = len(notes) >= len(pages)
        if c1.button(label, key=f"pages_{pid}", type="primary", disabled=done_all or not st.session_state.get("openai_key")):
            _pages_run(pid, note, pages, scope)
        c2.caption(f"{len(notes)} / {len(pages)}쪽 요약 · 쪽마다 한두 문장 요약, 핵심 용어, 생각해 볼 점이 나옵니다. 각 쪽 아래에 떠오른 생각을 적어 두면 ‘나의 생각’ 칸으로 모을 수 있습니다.")
        if not st.session_state.get("openai_key"):
            need_key_notice()
    if not notes:
        return
    c1, c2 = st.columns([1, 2])
    body_only = c1.checkbox("본문 쪽만 보기", value=True, key=f"bo_{pid}")
    if c2.button("쪽별 메모를 ‘생각과 의문’에 모으기", key=f"gather_{pid}"):
        n = _gather_memos(pid, note)
        st.toast(f"{n}개를 ‘생각과 의문’ 칸에 옮겼습니다." if n else "옮길 새 메모가 없습니다.")
    for n in sorted(notes, key=int):
        it = notes[n]
        if body_only and it.get("kind") != "본문":
            continue
        with st.container(border=True):
            badge = ""
            if it.get("quote") and pages:
                v = pdfio.verify_quote(pages, n, it["quote"])
                color = "green" if v == "일치" else "orange" if v.startswith("다른") else "red"
                badge = f" :{color}-badge[근거 {v}]"
            st.markdown(f":blue-badge[p.{n}] :gray[{it.get('kind', '')}]{badge}")
            st.write(it.get("summary", ""))
            if it.get("terms"):
                st.caption(" · ".join(it["terms"]))
            if it.get("question"):
                st.markdown(f"**생각해 볼 점** · {it['question']}")
            if it.get("kind") == "본문":
                key = f"memo_{pid}_{n}"
                if key not in st.session_state:
                    st.session_state[key] = memos.get(n, "")
                st.text_area(f"p.{n}을 읽고 든 생각", key=key, height=68, placeholder="이 쪽을 읽고 든 생각 (선택)", label_visibility="collapsed")
                memos[n] = st.session_state[key]


def _fmt(text: str) -> str:
    return re.sub(r"\[p\.?\s*(\d+(?:\s*[-–~,]\s*\d+)*)\]", lambda m: f":blue-badge[p.{m.group(1).replace(' ', '')}]", text)


def _chat_tab(pid: str, note: dict, material: str) -> None:
    chat = note.setdefault("chat", [])
    for m in chat:
        with st.chat_message("user" if m["role"] == "user" else "assistant"):
            st.markdown(_fmt(m["content"]) if m["role"] == "assistant" else m["content"])
    if not chat:
        st.caption("논문 내용을 물어보세요. 답에는 근거가 된 PDF 쪽이 붙습니다.")
    q = None
    cols = st.columns(len(SUGGEST))
    for col, s in zip(cols, SUGGEST):
        if col.button(s, key=f"sug_{pid}_{s[:6]}", width="stretch"):
            q = s
    with st.form(f"chat_{pid}", clear_on_submit=True):
        typed = st.text_area("질문", placeholder="예: 3.5절의 스냅숏 기반 분석 절차를 단계별로 알려 줘", height=80)
        if st.form_submit_button("질문 보내기", type="primary"):
            q = typed.strip() or None
            if not q:
                st.error("질문을 입력해 주세요.")
    if chat and st.button("대화 지우기", key=f"chatclr_{pid}"):
        note["chat"] = []
        st.rerun()
    st.caption("답은 PDF에서 추출한 글자만 근거로 합니다. 보고서 문장을 대신 써 달라는 요청에는 직접 쓰는 데 도움이 되는 질문과 근거로 답합니다.")
    if not q:
        return
    if not material:
        st.warning("PDF 또는 초록이 필요합니다.")
        return
    try:
        with st.spinner("논문을 읽고 답을 생각하는 중…"):
            res = ai.run_chat(st.session_state.get("openai_key", ""), st.session_state.get("ai_model", ai.MODELS[0]), material,
                              chat + [{"role": "user", "content": q}])
    except ai.AIError as exc:
        st.error(str(exc))
        return
    chat += [{"role": "user", "content": q}, {"role": "assistant", "content": res["text"]}]
    del chat[:-40]
    store.log(proj(), f"논문 질문 ({res['model']})")
    st.rerun()


def _ai_section(pid: str, note: dict, material: str, scope: dict):
    st.subheader("② AI 사전 요약 (직접 읽기 전 길잡이)")
    st.caption("먼저 쪽별 요약으로 논문 흐름을 훑고, 쪽마다 떠오른 생각을 적어 두세요. 궁금한 점은 ‘논문에 질문하기’에서 바로 물어볼 수 있습니다.")
    ready = need_key_notice() and bool(material)
    tabs = st.tabs(["쪽별 요약", "논문에 질문하기", "과제용 6항목", "읽기용 7문장", "기여도·증명방법·한계"])
    with tabs[0]:
        _pages_tab(pid, note, scope)
    with tabs[1]:
        _chat_tab(pid, note, material)
    for tab, task, label in ((tabs[2], "summary6", "6항목 요약 만들기"), (tabs[3], "summary7", "7문장 요약 만들기")):
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
    with tabs[4]:
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
    st.subheader("③ 나의 생각 (직접 작성)")
    note["read_state"] = st.select_slider("읽기 상태", READ_STATES, value=note.get("read_state", READ_STATES[0]), key=f"rs_{pid}")
    c1, c2 = st.columns([1, 3])
    if c1.button("질문 예시 받기", key=f"qs_{pid}", disabled=not ((material or note.get("page_notes")) and st.session_state.get("openai_key"))):
        body = [(n, v) for n, v in sorted((note.get("page_notes") or {}).items(), key=lambda x: int(x[0])) if v.get("kind") == "본문"]
        text = "[쪽별 요약]\n" + "\n".join(f"[PDF p.{n}] {v['summary']}" for n, v in body) if len(body) >= 5 else material
        gen = run_ai("questions", text, pid=pid, context=scope) if text else None
        if gen:
            def pick(k):
                out = []
                for o in gen["data"].get(k) or []:
                    o = {"q": o, "pages": []} if isinstance(o, str) else o
                    if isinstance(o, dict) and str(o.get("q", "")).strip():
                        out.append({"q": str(o["q"]).strip(), "pages": [p for p in (o.get("pages") or []) if str(p).isdigit()]})
                return out[:4]
            note["questions3"] = {k: pick(k) for _, _, _, k, _ in MY_FIELDS}
    c2.caption("세 칸마다 생각을 시작할 실마리 질문을 보여 줍니다. 질문에 대한 답은 본인 경험과 판단으로 직접 쓰세요. AI는 이 칸들을 채우거나 고치지 않습니다.")
    q3 = note.get("questions3") or {}
    for prefix, nkey, label, qkey, height in MY_FIELDS:
        key = f"{prefix}_{pid}"
        if key not in st.session_state:
            st.session_state[key] = note.get(nkey, "")
        st.text_area(label, key=key, height=height)
        note[nkey] = st.session_state[key]
        qs = q3.get(qkey) or []
        if qs:
            with st.container(border=True):
                st.caption("실마리 질문 (참고용)")
                st.markdown("\n".join(f"- {o['q']}" + (f" :gray[p.{', p.'.join(map(str, o['pages']))}]" if o.get("pages") else "") for o in qs))


def _tpl_editor() -> dict:
    tpl = exporters.report_tpl(proj())
    proj()["project"]["report_tpl"] = tpl
    v = tpl.get("_v", 0)
    with st.expander("보고서 양식 (항목·순서·제목 바꾸기)"):
        c1, c2 = st.columns([1, 2])
        preset = c1.selectbox("양식", reporttpl.PRESET_NAMES, index=reporttpl.PRESET_NAMES.index(tpl.get("preset", "기본")), key=f"tpl_preset_{v}")
        if preset != tpl.get("preset"):
            tpl["preset"] = preset
            if preset in reporttpl.PRESETS:
                tpl["sections"] = reporttpl.preset_sections(preset, tpl["sections"])
            tpl["_v"] = v + 1
            st.rerun()
        tpl["title"] = c2.text_input("보고서 제목", tpl.get("title", ""), key=f"tpl_title_{v}")
        changed, secs, n = False, tpl["sections"], 0
        for i, sec in enumerate(list(secs)):
            cols = st.columns([0.5, 0.5, 6, 0.6, 0.6], vertical_alignment="center")
            on = cols[0].checkbox(f"{reporttpl.SEC_DEF[sec['id']]} 넣기", value=sec["on"], key=f"tpl_on_{v}_{sec['id']}", label_visibility="collapsed")
            n += 1 if on else 0
            cols[1].markdown(f"{n}." if on else "")
            lab = cols[2].text_input(f"{reporttpl.SEC_DEF[sec['id']]} 제목", sec["label"], key=f"tpl_lab_{v}_{sec['id']}",
                                     placeholder=reporttpl.SEC_DEF[sec["id"]], label_visibility="collapsed")
            if on != sec["on"] or lab != sec["label"]:
                sec["on"], sec["label"], changed = on, lab, True
            if cols[3].button("↑", key=f"tpl_up_{v}_{sec['id']}", disabled=i == 0, help="위로"):
                secs[i - 1], secs[i] = secs[i], secs[i - 1]
                changed = True
            if cols[4].button("↓", key=f"tpl_dn_{v}_{sec['id']}", disabled=i == len(secs) - 1, help="아래로"):
                secs[i + 1], secs[i] = secs[i], secs[i + 1]
                changed = True
        st.caption("항목을 켜고 끄거나 순서를 바꾸고, 제목을 교수님 양식에 맞게 고칠 수 있습니다. 양식은 이 프로젝트의 모든 논문에 함께 적용됩니다.")
        if changed:
            tpl["preset"], tpl["_v"] = "직접 구성", v + 1
            st.rerun()
    return tpl


def _report(pid: str, note: dict):
    st.subheader("④ A4 과제 요약 보고서")
    tpl = _tpl_editor()
    on = {s["id"] for s in tpl["sections"] if s["on"]}
    f = exporters.report_fields(proj(), pid)
    labels = {"overview": "연구 개요", "tech": "활용 기술·분석 대상", "contribution": "기여 (1문장)", "proof": "증명방법 (1문장)",
              "scope": "검증 범위·한계 (1문장)", "limits": "논문에 명시된 한계"}
    sec_of = {"overview": "overview", "tech": "tech", "contribution": "three", "proof": "three", "scope": "three", "limits": "limits"}
    shown = {k: lab for k, lab in labels.items() if sec_of[k] in on}
    with st.form(f"report_{pid}"):
        st.caption("AI 초안을 고쳐 쓰세요. 고친 내용은 AI 초안과 따로 보관합니다. 선정 이유·나의 생각·추가 검토점은 ③에서 씁니다.")
        vals = {k: st.text_area(lab, f[k], height=70) for k, lab in shown.items()}
        replicated = st.checkbox("이 논문의 방법을 직접 따라 해 본 결과를 위 내용에 적었습니다", value=note.get("replicated", False))
        if st.form_submit_button("보고서 내용 저장", type="primary"):
            note["replicated"] = replicated
            edited = {k: v for k, v in note.get("edited", {}).items() if k not in shown}
            edited.update({k: v for k, v in vals.items() if v.strip() != f["_ai"][k].strip()})
            note["edited"] = edited
            store.log(proj(), "보고서 내용 저장")
            st.rerun()
    edited = [labels[k] for k in note.get("edited", {}) if k in shown]
    st.caption("AI 초안 그대로인 칸: " + (", ".join(v for k, v in shown.items() if k not in note.get("edited", {}) and f[k]) or "없음")
               + " · 내가 고친 칸: " + (", ".join(edited) or "없음"))
    blocks = exporters.report_blocks(proj(), pid, note.get("replicated", False))
    n = exporters.char_count(blocks)
    limit = reporttpl.PRESET_LIMIT.get(tpl.get("preset"), 1800)
    unit = "A4 두 쪽은 대략 3,200~3,600자" if tpl.get("preset") == "상세" else "A4 한 쪽은 대략 1,600~1,800자"
    if n > limit:
        st.warning(f"현재 약 {n:,}자입니다. {unit}여서 넘칠 수 있습니다. 글씨를 줄이지 말고 내용을 줄이거나 양식을 바꿔 주세요.")
    else:
        st.caption(f"현재 약 {n:,}자 ({unit}). 실제 쪽수는 내려받은 파일에서 확인해 주세요.")
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
        download_doc(blocks, "논문요약보고서", f"rep_{pid}", (tpl.get("title") or "논문 요약 보고서"))
    else:
        st.caption("최종 검토에 체크하면 Word·한글 파일을 내려받을 수 있습니다.")


def _delete_paper(pid: str) -> None:
    with st.expander("이 논문 삭제"):
        st.caption("서지정보, 요약, 쪽별 메모, 대화가 모두 지워지고 되돌릴 수 없습니다. 필요하면 먼저 왼쪽에서 프로젝트 파일을 내려받으세요.")
        if st.checkbox("삭제하겠습니다", key=f"delchk_{pid}") and st.button("이 논문 삭제", key=f"del_{pid}"):
            p = proj()
            for k in ("papers", "notes", "docs"):
                p[k].pop(pid, None)
            st.session_state.texts.pop(pid, None)
            p["adopted"] = [a for a in p["adopted"] if a.get("paper_id") != pid]
            store.log(p, "논문 삭제")
            st.session_state.pop("current_pid", None)
            for k in [k for k in st.session_state if isinstance(k, str) and k.endswith("_" + pid)]:
                del st.session_state[k]
            st.rerun()


def render():
    st.header("02. 논문 읽기")
    _register_direct()
    if not proj()["papers"]:
        st.info("01. 자료 찾기에서 논문을 고르거나, 위에서 직접 등록해 주세요.")
        return
    pid = pick_paper("read_pick")
    note = proj()["notes"].setdefault(pid, store.empty_note())
    for k, v in store.empty_note().items():
        note.setdefault(k, v)
    st.code(cite.format_citation(proj()["papers"][pid]), language=None, wrap_lines=True)
    material, scope = _source(pid, note)
    _ai_section(pid, note, material, scope)
    _thoughts(pid, note, material, scope)
    _report(pid, note)
    st.divider()
    if st.button("03. 선행연구 분석으로 보내기", help="서지정보, 요약, 근거, 나의 생각을 함께 넘깁니다."):
        note["in_lit"] = True
        goto("03. 선행연구 분석")
    _delete_paper(pid)
