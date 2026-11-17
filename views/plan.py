"""04. 연구 방향·계획 — 주제 후보, RQ, 방법·평가 설계, 연구계획서."""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import json
import time

import streamlit as st

from core import cite, store
from views import design as design_view
from views.common import download_doc, need_key_notice, proj, render_blocks, run_ai

FIELDS = ["제목", "배경·필요성", "선행연구·공백 후보", "목적·RQ", "방법·실험 설계", "평가 지표", "기대 기여(예상)", "일정", "제약"]
RQ_COLS = ["RQ", "필요한 근거", "데이터", "방법", "평가 지표", "결과로 확인할 사항", "실제 결과"]


def plan_blocks() -> list:
    plan = proj()["plan"]
    blocks = [("title", plan.get("제목") or "연구계획서"), ("meta", "연구 수행 전 계획서 — 결과는 포함하지 않습니다")]
    for i, k in enumerate(FIELDS[1:], start=1):
        blocks += [("h", f"{i}. {k}"), ("p", plan.get(k) or "[확인 필요]")]
    blocks.append(("h", f"{len(FIELDS)}. RQ 연결표"))
    for r in plan.get("rq_table", []):
        blocks.append(("p", " / ".join(f"{c}: {r.get(c) or '확인 필요'}" for c in RQ_COLS[:-1])))
    blocks.append(("h", f"{len(FIELDS) + 1}. 참고문헌"))
    for p in proj()["papers"].values():
        blocks.append(("p", cite.format_citation(p)))
    return blocks


def render():
    st.header("04. 연구 방향·계획")
    tabs = st.tabs(["연구계획서", "나의 연구 설계안 (Research Paper)"])
    with tabs[0]:
        _plan_tab()
    with tabs[1]:
        design_view.render_tab()


def _plan_tab():
    info = proj()["project"]
    with st.form("proj_info"):
        st.markdown("**연구 조건** (모르는 칸은 비워 두면 ‘확인 필요’로 처리합니다)")
        c = st.columns(2)
        info["topic"] = c[0].text_input("주제", info.get("topic", ""))
        info["purpose"] = c[1].text_input("학위·과제 목적", info.get("purpose", ""))
        info["period"] = c[0].text_input("연구 기간", info.get("period", ""))
        info["advisor"] = c[1].text_input("지도교수 요구사항", info.get("advisor", ""))
        info["resources"] = st.text_area("확보할 수 있는 자료·도구·예산·실험 환경", info.get("resources", ""), height=70)
        st.form_submit_button("저장")
    adopted = proj()["adopted"]
    if not adopted:
        st.info("03. 선행연구 분석에서 채택한 아이디어가 여기에 표시됩니다. 아이디어 없이 계획서 칸을 직접 채워도 됩니다.")
    else:
        idx = st.radio("계획서로 발전시킬 후보", range(len(adopted)), format_func=lambda i: adopted[i].get("아이디어", f"후보 {i + 1}"))
        if st.button("연구계획서 초안 만들기", disabled=not need_key_notice()):
            refs = [cite.format_citation(p) for p in proj()["papers"].values()]
            gen = run_ai("plan", json.dumps({"채택 아이디어": adopted[idx], "연구 조건": {k: info.get(k) or "확인 필요" for k in ("topic", "purpose", "period", "advisor", "resources")},
                                             "읽은 문헌": refs}, ensure_ascii=False, indent=1))
            if gen:
                if proj()["plan"]:
                    proj()["plan_log"].append({"at": time.strftime("%Y-%m-%d %H:%M"), "reason": "AI 초안으로 다시 생성", "previous": dict(proj()["plan"])})
                proj()["plan"] = gen["data"] | {"_ai": True}
    plan = proj()["plan"]
    st.subheader("연구계획서")
    with st.form("plan_edit"):
        vals = {k: (st.text_input(k, plan.get(k, "")) if k == "제목" else st.text_area(k, plan.get(k, ""), height=90)) for k in FIELDS}
        st.markdown("**RQ 연결표** — ‘실제 결과’는 연구를 수행한 뒤 05 메뉴에서 채웁니다.")
        table = st.data_editor([{c: r.get(c, "") for c in RQ_COLS} for r in plan.get("rq_table", [])] or [{c: "" for c in RQ_COLS}],
                               num_rows="dynamic", width="stretch", key="rq_editor")
        reason = st.text_input("변경 이유 또는 지도교수 검토 의견 (선택)")
        if st.form_submit_button("계획서 저장", type="primary"):
            if plan and (reason or any(vals[k] != plan.get(k, "") for k in FIELDS)):
                proj()["plan_log"].append({"at": time.strftime("%Y-%m-%d %H:%M"), "reason": reason or "사용자 수정", "previous": {k: plan.get(k, "") for k in FIELDS}})
            proj()["plan"] = {**vals, "rq_table": [r for r in table if any(str(v).strip() for v in r.values())], "_ai": False}
            store.log(proj(), "연구계획서 저장")
            st.rerun()
    if plan.get("_ai"):
        st.caption("지금 내용은 AI 초안입니다. 읽고 고친 뒤 저장해 주세요.")
    st.caption("‘기대 기여’는 예상입니다. 입증된 기여가 아니며, 문헌 조사만으로 연구 공백이 증명된 것도 아닙니다. 일정은 가정에 따른 계획입니다.")
    if plan:
        with st.expander("계획서 미리보기와 내려받기", expanded=True):
            blocks = plan_blocks()
            with st.container(border=True):
                render_blocks(blocks)
            download_doc(blocks, "연구계획서", "plan", plan.get("제목") or "연구계획서")
    if proj()["plan_log"]:
        with st.expander(f"변경 이력 ({len(proj()['plan_log'])}건)"):
            st.dataframe([{"시각": x["at"], "이유": x["reason"], "이전 제목": x["previous"].get("제목", "")} for x in proj()["plan_log"]], hide_index=True, width="stretch")
