"""03. 선행연구 분석 — 논문별·논문 간 비교, 브레인스토밍, 연구 공백 후보."""
from __future__ import annotations

import json

import streamlit as st

from core import cite, exporters
from views.common import goto, need_key_notice, paper_label, proj, run_ai

COLS = ["주요 내용", "활용 기술", "연구방법·증명방법", "연구 결과", "연구 기여도", "연구 한계", "새로운 쟁점·공백 후보"]


def note_digest(pid: str) -> dict:
    """AI에 넘기는 논문별 읽기노트. 사용자가 실제로 쓴 값과 AI 요약을 구분해 담는다."""
    note = proj()["notes"].get(pid, {})
    f = exporters.report_fields(proj(), pid)
    s7 = {k: (v or {}).get("text", "") for k, v in ((note.get("summary7") or {}).get("data") or {}).get("items", {}).items()}
    sec = {k: (v or {}).get("text", "") for k, v in ((note.get("method") or {}).get("sections") or {}).items()}
    return {"paper_id": pid, "참고문헌": cite.format_citation(proj()["papers"][pid]), "확인 범위": note.get("source_scope", "") or "원문 미확인",
            "개요": f["overview"], "기술": f["tech"], "방법·증명": f["proof"] or sec.get("연구방법·증명방법", ""), "결과": sec.get("연구 결과", "") or s7.get("주요 연구 결과", ""),
            "기여": f["contribution"], "한계": f["limits"], "사용자 의문": note.get("my_thoughts", "")}


def render():
    st.header("03. 선행연구 분석")
    ids = [pid for pid, n in proj()["notes"].items() if pid in proj()["papers"]]
    if not ids:
        st.info("02. 논문 읽기에서 읽기노트를 만든 논문이 여기에 모입니다.")
        return
    default = [pid for pid in ids if proj()["notes"][pid].get("in_lit")] or ids
    labels = {i: paper_label(i) for i in ids}
    chosen = st.multiselect("비교할 논문", ids, default=default, format_func=lambda i: labels.get(i, i))
    if not chosen:
        return
    digests = [note_digest(pid) for pid in chosen]
    unread = [d for d in digests if d["확인 범위"] == "원문 미확인"]
    if len(chosen) == 1:
        st.info("논문이 한 편이면 단일 연구 분석입니다. 여러 선행연구를 비교한 것으로 볼 수 없습니다.")
    st.caption(f"비교 대상 {len(chosen)}편 · 원문 또는 초록을 확인한 논문 {len(chosen) - len(unread)}편"
               + (f" · 원문을 읽지 않은 논문 {len(unread)}편은 서지정보만 있습니다" if unread else ""))

    st.subheader("① 논문 비교표")
    if st.button("비교표 만들기", disabled=not need_key_notice()):
        gen = run_ai("compare", json.dumps(digests, ensure_ascii=False, indent=1))
        if gen:
            proj()["comparison"] = {"ids": chosen, "data": gen["data"], "created": gen["created"], "model": gen["model"]}
    comp = proj().get("comparison") or {}
    if comp.get("data"):
        rows = []
        for r in comp["data"].get("rows", []):
            pid = r.get("paper_id")
            rows.append({"정식 참고문헌": cite.format_citation(proj()["papers"][pid]) if pid in proj()["papers"] else "[등록되지 않은 논문]",
                         **{c: r.get(c, "") for c in COLS}})
        st.dataframe(rows, width="stretch", hide_index=True)
        st.write(comp["data"].get("cross", ""))
        if set(comp["ids"]) != set(chosen):
            st.caption("⚠ 선택한 논문이 비교표를 만들 때와 다릅니다. 다시 만들어 주세요.")
        st.caption("‘새로운 쟁점·공백 후보’는 읽기노트만 보고 낸 후보입니다. 문헌 전체에서 아직 풀리지 않은 문제라는 뜻이 아니며, 추가 검색으로 확인해야 합니다.")
        st.download_button("비교표 Excel 내려받기", exporters.to_xlsx({"선행연구 비교": rows}), f"선행연구비교_{exporters.stamp()}.xlsx")

    st.subheader("② 나의 의문에서 연구 아이디어로")
    thoughts = "\n".join(f"- {d['사용자 의문']}" for d in digests if d["사용자 의문"].strip())
    if not thoughts:
        st.warning("02 메뉴의 ‘나의 생각·의문’이 비어 있습니다. 본인의 의문이 있어야 아이디어를 낼 수 있습니다.")
        return
    constraints = st.text_area("연구 제약 (기간, 구할 수 있는 자료·도구 등)", proj()["project"].get("resources", ""), height=70)
    prompt = st.text_area("AI에 보낼 탐색 요청 (실행 전에 고칠 수 있습니다)",
                          f"내가 쓴 의문:\n{thoughts}\n\n연구 제약:\n{constraints or '확인 필요'}", height=160)
    if st.button("아이디어 3개 제안받기", disabled=not st.session_state.get("openai_key")):
        gen = run_ai("ideas", prompt + "\n\n읽기노트:\n" + json.dumps(digests, ensure_ascii=False))
        if gen:
            proj()["ideas"] = gen["data"].get("ideas", [])
    for i, idea in enumerate(proj().get("ideas", [])):
        with st.container(border=True):
            st.markdown(f"**아이디어 {i + 1}: {idea.get('아이디어', '')}**")
            for k in ("출발 의문", "기존 연구의 근거", "잠정 RQ", "필요한 자료", "추가 검증"):
                st.write(f"- **{k}** — {idea.get(k, '')}")
            adopted = idea in proj()["adopted"]
            if st.checkbox("이 후보를 연구계획으로 가져가기", value=adopted, key=f"adopt_{i}") != adopted:
                (proj()["adopted"].remove if adopted else proj()["adopted"].append)(idea)
                st.rerun()
    if proj().get("ideas"):
        st.caption("아이디어를 여러 개 냈다고 독창성이 확인된 것은 아닙니다. 채택한 후보만 04로 넘어갑니다.")
    if proj()["adopted"] and st.button("04. 연구 방향·계획으로 이동", type="primary"):
        goto("04. 연구 방향·계획")
