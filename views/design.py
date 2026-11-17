"""04-② 나의 연구 설계안 — 장별 인터뷰 → 장별 초안 → 초록·주제어·제목 → Word·한글.

목차는 Research Paper(연구 설계안) 양식을 따른다: 초록·주제어, Ⅰ~Ⅵ장, 참고문헌.
학생이 답한 내용이 글의 중심이며, 답하지 않은 내용은 [확인 필요]로 남긴다.
"""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import pandas as pd
import streamlit as st

from core import cite, design, exporters, store
from views.common import download_doc, need_key_notice, proj, render_blocks, run_prompt


def _d() -> dict:
    proj()["design"] = design.ensure(proj().get("design"))
    return proj()["design"]


def _citations() -> list[tuple[str, str]]:
    return [(pid, cite.format_citation(p)) for pid, p in proj()["papers"].items()]


def _material() -> str:
    info = proj()["project"]
    out = [f"[연구 조건] 주제: {info.get('topic') or '확인 필요'} / 목적: {info.get('purpose') or '확인 필요'} / "
           f"자료·환경: {info.get('resources') or '확인 필요'} / 지도교수 요구: {info.get('advisor') or '없음'}"]
    for pid, p in proj()["papers"].items():
        f = exporters.report_fields(proj(), pid)
        parts = [f"[읽은 논문] {cite.format_citation(p)}"]
        for k, lab in (("overview", "연구 개요"), ("tech", "활용 기술·분석 대상"), ("contribution", "기여"), ("proof", "증명방법"),
                       ("scope", "검증 범위"), ("limits", "한계")):
            if str(f.get(k) or "").strip():
                parts.append(f"{lab}: {f[k]}")
        for k, lab in (("reason", "선정 이유(학생)"), ("thoughts", "생각과 의문(학생)"), ("extra_limits", "추가 검토점(학생)")):
            if str(f.get(k) or "").strip():
                parts.append(f"{lab}: {f[k]}")
        out.append("\n".join(parts))
    plan = proj().get("plan") or {}
    if plan.get("목적·RQ"):
        out.append(f"[연구계획서 탭] 목적·RQ: {plan['목적·RQ']} / 방법: {plan.get('방법·실험 설계', '')}")
    return "\n\n".join(out)[:40000]


def _put(key: str, value: str) -> None:
    st.session_state[key] = value


def _header(d: dict) -> None:
    for k, lab, ph in (("title", "논문 제목 (가안)", "예: Amazon RDS 로그 수집 구성에 따른 디지털포렌식 증거 확보 수준 비교 연구"),
                       ("author", "작성자·소속", "예: 정보보호대학원 ○○○"), ("sub", "부제", "")):
        key = f"rp_{k}"
        if key not in st.session_state:
            st.session_state[key] = d.get(k, "")
        st.text_input(lab, key=key, placeholder=ph)
        d[k] = st.session_state[key]
    cites = _citations()
    with st.expander(f"참고문헌 ({len(design.ref_list(d, cites))}개)"):
        if cites:
            labels = {pid: c for pid, c in cites}
            key = "rp_refs"
            if key not in st.session_state:
                st.session_state[key] = [pid for pid, _ in cites if d["refs"] is None or pid in d["refs"]]
            st.multiselect("읽은 논문 중 참고문헌에 넣을 것", list(labels), key=key, format_func=lambda x: labels.get(x, x)[:90])
            d["refs"] = [x for x in st.session_state[key] if x in labels]
        if "rp_extra" not in st.session_state:
            st.session_state["rp_extra"] = d.get("extra", "")
        st.text_area("추가 참고문헌 (직접 확인한 문헌만, 한 줄에 하나)", key="rp_extra", height=80)
        d["extra"] = st.session_state["rp_extra"]
        st.caption("초안의 인용 번호 [n]은 이 목록 순서를 따릅니다. AI는 목록에 없는 문헌·법령 조항을 새로 만들지 않도록 지시받습니다.")


def _interview(d: dict, cid: str, ch: dict, refs: list[str], ready: bool) -> None:
    c = design.CH_BY_ID[cid]
    st.caption("이 장에서 다룰 내용 · " + c["guide"])
    if ch["questions"] is None:
        b = st.columns(2)
        if b[0].button("AI 인터뷰 질문 받기", key=f"rp_q_{cid}", type="primary", disabled=not ready):
            r = run_prompt(design.SYSTEM, design.question_prompt(cid, design.refs_text(refs), _material(), design.previous(d, cid)),
                           "design_q", "인터뷰 질문을 만드는 중…")
            qs = design.norm_questions(r or {})
            if qs:
                ch.update({"questions": qs, "answers": {}, "qver": ch["qver"] + 1})
                st.rerun()
            elif r is not None:
                st.warning("질문을 만들지 못했습니다. 다시 눌러 주세요.")
        if b[1].button("기본 질문으로 시작 (AI 없이)", key=f"rp_qb_{cid}"):
            ch.update({"questions": design.basic_questions(cid), "answers": {}, "qver": ch["qver"] + 1})
            st.rerun()
        return
    for i, q in enumerate(ch["questions"]):
        with st.container(border=True):
            st.markdown(f"**Q{i + 1}.** {q['q']}")
            if q.get("why"):
                st.caption(q["why"])
            if q.get("example"):
                with st.expander("예시 답 보기 (참고용, 내 생각으로 바꿔 쓰세요)"):
                    st.write(q["example"])
            k = f"rp_a_{cid}_{ch['qver']}_{i}"
            if k not in st.session_state:
                st.session_state[k] = ch["answers"].get(str(i), "")
            st.text_area(f"Q{i + 1} 답", key=k, height=80, label_visibility="collapsed", placeholder="짧은 메모로 적어도 됩니다")
            ch["answers"][str(i)] = st.session_state[k]
    k = f"rp_free_{cid}"
    if k not in st.session_state:
        st.session_state[k] = ch.get("free", "")
    st.text_area("이 장에 더 넣고 싶은 내용 (선택)", key=k, height=70)
    ch["free"] = st.session_state[k]
    b = st.columns([2, 1])
    if b[0].button("이 장 초안 만들기" if not ch.get("draft") else "답변으로 초안 다시 만들기", key=f"rp_dr_{cid}", type="primary",
                   disabled=not (ready and design.has_answers(ch))):
        r = run_prompt(design.SYSTEM, design.draft_prompt(cid, design.refs_text(refs), design.answers_text(ch), _material(), design.previous(d, cid)),
                       "design_draft", f"{c['title']} 초안을 쓰는 중…")
        dr = design.norm_draft(r or {})
        if dr:
            ch.update({"prev_draft": ch.get("draft"), "draft": dr, "dver": ch["dver"] + 1})
            store.log(proj(), f"연구 설계안 초안: {c['title']}")
            st.rerun()
        elif r is not None:
            st.warning("초안을 만들지 못했습니다. 다시 눌러 주세요.")
    if b[1].button("질문 다시 받기", key=f"rp_qr_{cid}", help="지금까지 쓴 답은 지워집니다. 초안은 남습니다."):
        ch.update({"questions": None, "answers": {}})
        st.rerun()


def _df(t: dict) -> pd.DataFrame:
    cols, seen = [], set()
    for h in t["header"]:
        h = h or " "
        while h in seen:
            h += " "
        seen.add(h)
        cols.append(h)
    return pd.DataFrame(t["rows"] or [[""] * len(cols)], columns=cols)


def _draft_editor(cid: str, ch: dict) -> None:
    dr = ch.get("draft")
    if not dr:
        return
    st.markdown("##### 초안 (바로 고쳐 쓸 수 있습니다)")
    v = ch["dver"]
    for i, s in enumerate(dr["sections"]):
        with st.container(border=True):
            kt, kp = f"rp_st_{cid}_{v}_{i}", f"rp_sp_{cid}_{v}_{i}"
            if kt not in st.session_state:
                st.session_state[kt] = s.get("title", "")
                st.session_state[kp] = "\n\n".join(s.get("paragraphs", []))
            st.text_input("소절 제목", key=kt, placeholder="소절이 없는 장은 비워 둡니다")
            st.text_area("문단 (빈 줄로 문단 구분)", key=kp, height=200)
            s["title"] = st.session_state[kt]
            s["paragraphs"] = [x.strip() for x in str(st.session_state[kp]).split("\n\n") if x.strip()]
            t = s.get("table")
            if t:
                kc = f"rp_tc_{cid}_{v}_{i}"
                if kc not in st.session_state:
                    st.session_state[kc] = t.get("caption", "")
                st.text_input("표 제목 (번호는 자동)", key=kc)
                t["caption"] = st.session_state[kc]
                df = st.data_editor(_df(t), num_rows="dynamic", width="stretch", hide_index=True, key=f"rp_tb_{cid}_{v}_{i}")
                t["rows"] = [[("" if pd.isna(x) else str(x)) for x in row] for row in df.itertuples(index=False)
                             if any(str(x).strip() and not pd.isna(x) for x in row)]
    if dr.get("needs"):
        st.warning("보완할 것 · " + " / ".join(dr["needs"]))
    if ch.get("prev_draft") and st.button("이전 초안으로 되돌리기", key=f"rp_prev_{cid}"):
        ch["draft"], ch["prev_draft"], ch["dver"] = ch["prev_draft"], ch["draft"], v + 1
        st.rerun()


def _abstract(d: dict, ready: bool) -> None:
    st.markdown("#### 초록 · 주제어 · 제목 후보")
    has = any(d["ch"][c["id"]].get("draft") for c in design.CHAPTERS)
    if st.button("초록·주제어·제목 후보 만들기", key="rp_abs", disabled=not (ready and has), help="장별 초안을 바탕으로 만듭니다."):
        r = run_prompt(design.SYSTEM, design.abstract_prompt(d), "design_abs", "초록을 쓰는 중…")
        if r and r.get("abstract"):
            d["abs"] = {"abstract": [str(x) for x in r["abstract"] if str(x).strip()][:4], "keywords": [str(x) for x in (r.get("keywords") or [])][:8],
                        "titles": [str(x) for x in (r.get("titles") or [])][:3], "v": int((d.get("abs") or {}).get("v", 0)) + 1}
            st.rerun()
    if not has:
        st.caption("장별 초안을 하나 이상 만든 뒤 누를 수 있습니다.")
    a = d.get("abs")
    if not a:
        return
    v = a.get("v", 0)
    ka = f"rp_abst_{v}"
    if ka not in st.session_state:
        st.session_state[ka] = "\n\n".join(a["abstract"])
    st.text_area("초록 (빈 줄로 문단 구분)", key=ka, height=220)
    a["abstract"] = [x.strip() for x in str(st.session_state[ka]).split("\n\n") if x.strip()]
    kk = f"rp_kw_{v}"
    if kk not in st.session_state:
        st.session_state[kk] = ", ".join(a["keywords"])
    st.text_input("주제어 (쉼표로 구분)", key=kk)
    a["keywords"] = [x.strip() for x in str(st.session_state[kk]).split(",") if x.strip()]
    for i, t in enumerate(a.get("titles") or []):
        c = st.columns([5, 1], vertical_alignment="center")
        c[0].markdown(f"제목 후보 {i + 1} · {t}")
        c[1].button("이 제목 쓰기", key=f"rp_tu_{v}_{i}", on_click=_put, args=("rp_title", t))


def render_tab() -> None:
    d = _d()
    ready = bool(st.session_state.get("openai_key"))
    st.caption("Research Paper(연구 설계안) 양식의 장마다 AI가 인터뷰 질문을 하고, 내 답을 중심으로 초안을 씁니다. "
               "아직 수행하지 않은 연구이므로 결과는 ‘예정’으로 쓰고, 답하지 않은 내용은 [확인 필요]로 남깁니다.")
    if not ready:
        need_key_notice()
    _header(d)
    refs = design.ref_list(d, _citations())
    labels = {c["id"]: f"{c['title'].split('.')[0]}. {design.status(d['ch'][c['id']])}" for c in design.CHAPTERS}
    if "rp_cur" not in st.session_state:
        st.session_state["rp_cur"] = d["cur"]
    st.radio("장 선택", list(labels), key="rp_cur", horizontal=True, format_func=lambda x: labels[x])
    d["cur"] = st.session_state["rp_cur"]
    cid = d["cur"]
    st.markdown(f"### {design.CH_BY_ID[cid]['title']}")
    ch = d["ch"][cid]
    _interview(d, cid, ch, refs, ready)
    _draft_editor(cid, ch)
    st.divider()
    _abstract(d, ready)
    st.divider()
    blocks = design.blocks(d, refs)
    with st.expander("설계안 미리보기와 내려받기"):
        with st.container(border=True):
            render_blocks(blocks)
        if st.checkbox("내용을 직접 읽고 [확인 필요]를 채웠습니다 (제출 전 최종 검토)", key="rp_final"):
            download_doc(blocks, "연구설계안", "rp", d.get("title") or "연구 설계안")
        else:
            st.caption("최종 검토에 체크하면 Word·한글 파일을 내려받을 수 있습니다.")
