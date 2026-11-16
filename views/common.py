"""화면 공통 도우미."""
from __future__ import annotations

import os

import streamlit as st

from core import ai, cite, exporters, store

MENUS = ["01. 자료 찾기", "02. 논문 읽기", "03. 선행연구 분석", "04. 연구 방향·계획", "05. 연구 수행·분석", "06. 내 논문 작성", "관리자: AI 품질평가"]


def proj() -> dict:
    return st.session_state.proj


def secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value or os.environ.get(name, "")).strip()


def goto(menu: str) -> None:
    st.session_state._goto = menu
    st.rerun()


def steps(labels: list[tuple[str, str]], current: int = 0) -> None:
    cols = st.columns(len(labels))
    for i, (col, (title, sub)) in enumerate(zip(cols, labels), start=1):
        with col.container(border=True):
            mark = "▶ " if i == current else ""
            st.markdown(f"**{mark}{i}. {title}**")
            st.caption(sub)


def paper_label(pid: str) -> str:
    p = cite.effective(proj()["papers"][pid])
    return f"{p.get('title') or '[제목 미확인]'} | {p.get('author') or '저자 미확인'} | {p.get('year') or '연도 미확인'}"


def pick_paper(key: str, only_with_notes: bool = False) -> str | None:
    ids = [pid for pid in proj()["papers"] if not only_with_notes or pid in proj()["notes"]]
    if not ids:
        return None
    cur = st.session_state.get("current_pid")
    if st.session_state.get(key) not in ids or (cur in ids and st.session_state.get("_synced_pid") != cur):
        st.session_state[key] = cur if cur in ids else ids[0]
    labels = {i: paper_label(i) for i in ids}
    pid = st.selectbox("논문 선택", ids, format_func=lambda i: labels.get(i, i), key=key)
    st.session_state.current_pid = st.session_state._synced_pid = pid
    return pid


def run_ai(task: str, material: str, extra: str = "", pid: str = "", context: dict | None = None) -> dict | None:
    """버튼 클릭 안에서만 부른다. 실패하면 안내만 하고 None."""
    try:
        with st.spinner("AI가 분석하고 있습니다…"):
            gen = ai.run_task(st.session_state.get("openai_key", ""), st.session_state.get("ai_model", ai.MODELS[0]), task, material, extra)
    except ai.AIError as exc:
        st.error(str(exc))
        return None
    gen.update({"paper_id": pid, "context": context or {}})
    proj()["generations"].append({k: v for k, v in gen.items()})
    store.log(proj(), f"AI 실행: {task} ({gen['model']})")
    return gen


def need_key_notice() -> bool:
    if st.session_state.get("openai_key", "").strip():
        return True
    st.info("AI 기능을 쓰려면 왼쪽 ‘개인 AI 설정’에 본인의 OpenAI API 키를 입력해 주세요. 검색·원문 읽기·노트 저장은 키 없이도 됩니다.")
    return False


def download_doc(blocks, basename: str, key: str, title: str = "문서") -> None:
    c1, c2 = st.columns(2)
    c1.download_button("Word(.docx) 내려받기", exporters.to_docx(blocks), f"{basename}_{exporters.stamp()}.docx",
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document", key=key + "_docx", width="stretch")
    c2.download_button("한글(.hwpx) 내려받기", exporters.to_hwpx(blocks, title), f"{basename}_{exporters.stamp()}.hwpx",
                       "application/hwp+zip", key=key + "_hwpx", width="stretch")


def render_blocks(blocks) -> None:
    for kind, text in blocks:
        if kind == "title":
            st.markdown(f"<div style='text-align:center;font-size:1.25rem;font-weight:600'>{text}</div>", unsafe_allow_html=True)
        elif kind == "meta":
            st.caption(text)
        elif kind == "h":
            st.markdown(f"**{text}**")
        elif kind == "b":
            label, _, body = text.partition("|")
            st.markdown(f"**{label}:** {body}")
        else:
            st.write(text)
