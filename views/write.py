"""06. 내 논문 작성 — 목차, 장별 초안, 인용·정합성 검토, 내보내기."""
from __future__ import annotations

import json

import streamlit as st

from core import cite, store
from views.common import download_doc, need_key_notice, proj, render_blocks, run_ai
from views.lit import note_digest

CHAPTERS = ["Abstract", "Introduction", "Related Work", "Methods", "Results", "Discussion", "Conclusion"]
STATES = {"주제·논문만 있음": ["Introduction", "Related Work"],
          "계획은 있으나 연구 전": ["Introduction", "Related Work", "Methods"],
          "실제 결과 있음": CHAPTERS, "초안 있음": CHAPTERS}
NEED_RESULT = "[실제 연구 결과 입력 필요]"


def _material(chapter: str) -> str:
    p = proj()
    results = [{"이름": e["title"], "RQ": e["rq"], "표": e["table"][:30], "전처리": e["preprocessing"]} for e in p["experiments"] if e["kind"] == "result"]
    logs = [{k: e.get(k) for k in ("title", "env", "conditions", "outcome", "memo")} for e in p["experiments"] if e["kind"] == "log"]
    return json.dumps({"쓸 장": chapter, "연구계획": {k: v for k, v in p["plan"].items() if not k.startswith("_")},
                       "읽은 문헌 노트": [note_digest(pid) for pid in p["notes"] if pid in p["papers"]],
                       "실제 결과(05에서 등록)": results or "없음", "실험 기록": logs or "없음",
                       "이미 쓴 초안": {k: v for k, v in p["manuscript"].get("chapters", {}).items() if v}}, ensure_ascii=False, indent=1)


def blocks() -> list:
    ms = proj()["manuscript"]
    out = [("title", ms.get("title") or proj()["plan"].get("제목") or "[논문 제목]")]
    for ch in CHAPTERS:
        out += [("h", ch), ("p", ms.get("chapters", {}).get(ch) or (NEED_RESULT if ch == "Results" else "[작성 필요]"))]
    out.append(("h", "References"))
    out += [("p", f"[{i}] {cite.format_citation(p)}") for i, p in enumerate(proj()["papers"].values(), start=1)]
    return out


def render():
    st.header("06. 내 논문 작성")
    ms = proj()["manuscript"]
    ms.setdefault("chapters", {})
    ms.setdefault("ai", {})
    has_result = any(e["kind"] == "result" for e in proj()["experiments"])
    state = st.radio("지금 내 상태", list(STATES), horizontal=True, index=list(STATES).index(ms.get("state", "주제·논문만 있음")))
    ms["state"] = state
    if state in ("실제 결과 있음", "초안 있음") and not has_result:
        st.warning("05 메뉴에 등록한 실제 결과가 없습니다. 계획서와 Introduction은 쓸 수 있지만 Results에는 실제 결과가 필요합니다.")
    ms["title"] = st.text_input("논문 제목", ms.get("title") or proj()["plan"].get("제목", ""))
    ms["format"] = st.text_input("제출 형식 (학교·학회·과제 양식 이름과 요구 사항)", ms.get("format", ""))
    allowed = STATES[state]
    ch = st.selectbox("작성할 장", CHAPTERS, format_func=lambda c: c if c in allowed else f"{c} (지금 상태에서는 AI 초안 미지원)")
    blocked = None
    if ch not in allowed:
        blocked = "지금 상태에서는 이 장의 AI 초안을 만들지 않습니다. 수행하지 않은 연구의 내용을 만들어 내지 않기 위해서입니다. 직접 쓰는 것은 가능합니다."
    elif ch in ("Results", "Discussion") and not has_result:
        blocked = "Results와 Discussion의 AI 초안에는 05 메뉴에 등록한 실제 결과가 필요합니다."
    if blocked:
        st.info(blocked)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**내 원고** (최종 원고에 들어가는 칸)")
        text = st.text_area("내 원고", ms["chapters"].get(ch, ""), height=360, key=f"ch_{ch}", label_visibility="collapsed")
        if st.button("내 원고 저장", type="primary"):
            if ms["chapters"].get(ch) and ms["chapters"][ch] != text:
                ms.setdefault("versions", []).append({"chapter": ch, "text": ms["chapters"][ch]})
                del ms["versions"][:-30]
            ms["chapters"][ch] = text
            store.log(proj(), f"원고 저장: {ch}")
            st.rerun()
    with c2:
        st.markdown("**AI 제안** (내 원고에 자동으로 들어가지 않습니다)")
        b1, b2 = st.columns(2)
        ok = need_key_notice()
        if b1.button("이 장 초안 제안받기", disabled=bool(blocked) or not ok):
            extra = f"쓸 장: {ch}. 제출 형식: {ms.get('format') or '지정 없음'}. 상태: {state}."
            gen = run_ai("draft", _material(ch), extra)
            if gen:
                ms["ai"][ch] = {"draft": gen["data"], "created": gen["created"]}
        if b2.button("내 원고 검토받기", disabled=not ok or not ms["chapters"].get(ch)):
            gen = run_ai("review", _material(ch), f"검토할 장: {ch}\n원고:\n{ms['chapters'][ch]}")
            if gen:
                ms["ai"].setdefault(ch, {})["review"] = gen["data"].get("issues", [])
        prop = ms["ai"].get(ch, {})
        if prop.get("draft"):
            with st.container(border=True, height=250):
                st.write(prop["draft"].get("text", ""))
            if prop["draft"].get("needs"):
                st.caption("직접 채워야 할 것: " + " / ".join(map(str, prop["draft"]["needs"])))
            if st.button("이 제안을 내 원고 칸으로 가져오기", help="현재 내 원고는 이전 판으로 보관한 뒤 바꿉니다. 가져온 뒤 직접 고쳐 쓰세요."):
                if ms["chapters"].get(ch):
                    ms.setdefault("versions", []).append({"chapter": ch, "text": ms["chapters"][ch]})
                ms["chapters"][ch] = prop["draft"].get("text", "")
                st.session_state.pop(f"ch_{ch}", None)
                st.rerun()
        if prop.get("review"):
            st.dataframe(prop["review"], hide_index=True, width="stretch")
            st.caption("지적 사항입니다. 반영할지는 직접 판단하세요.")
    st.caption("참고문헌은 읽은 논문의 정식 서지에서 자동으로 만듭니다. 원문을 직접 읽지 않은 문헌은 인용하지 마세요. AI가 만든 문장은 그대로 제출하지 말고 본인 글로 고쳐 쓰세요.")
    with st.expander("전체 원고 미리보기와 내려받기"):
        bl = blocks()
        with st.container(border=True):
            render_blocks(bl)
        md = "\n\n".join(("# " if k == "title" else "## " if k == "h" else "") + t for k, t in bl)
        st.download_button("Markdown(.md) 내려받기", md.encode("utf-8"), "원고.md", "text/markdown")
        download_doc(bl, "원고", "ms", ms.get("title") or "원고")
