"""대학원 AI 논문 연구 도우미 — Streamlit 진입점.

실행: streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from core import ai, store
from core.version import APP_VERSION, BUILD_ID
from views import common

st.set_page_config(page_title="대학원 AI 논문 연구 도우미", page_icon="🎓", layout="wide")

ss = st.session_state
if "proj" not in ss:
    ss.proj = store.new_project()
    ss.texts = {}        # paper_id → 추출한 쪽별 본문 (세션에만 둠)
    ss.run = None        # 최근 검색 실행
    ss.menu = common.MENUS[0]
if "_goto" in ss:
    ss.menu = ss.pop("_goto")

with st.sidebar:
    st.markdown("### 🎓 논문 연구 도우미")
    st.caption(f"v{APP_VERSION} · 빌드 {BUILD_ID}")
    st.radio("연구 진행 단계", common.MENUS, key="menu")
    st.divider()
    st.markdown("**개인 AI 설정**")
    st.text_input("본인의 OpenAI API 키", type="password", key="openai_key", placeholder="sk-...",
                  help="이 브라우저 세션에만 보관하며 서버·프로젝트 파일·로그에 저장하지 않습니다.")
    st.selectbox("AI 모델", ai.MODELS, key="ai_model", accept_new_options=True,
                 help="목록에 없는 모델 이름은 직접 입력할 수 있습니다.")
    st.caption("키가 없어도 검색, 원문 읽기, 노트 저장, 보고서 내려받기는 됩니다.")
    st.divider()
    with st.expander("과제 보고서 표지"):
        cover = ss.proj["project"]["cover"]
        for k, label in (("school", "학교"), ("major", "전공"), ("course", "교과목"), ("name", "성명"), ("date", "제출일")):
            cover[k] = st.text_input(label, cover.get(k, ""), key=f"cover_{k}")
    with st.expander("프로젝트 저장·불러오기"):
        st.caption("작업은 이 브라우저 세션에만 보관됩니다. 창을 닫기 전에 파일로 내려받으세요. API 키와 원문 PDF는 파일에 들어가지 않습니다.")
        st.download_button("프로젝트 파일(JSON) 내려받기", store.export_project(ss.proj), "paper_project.json", "application/json", width="stretch")
        up = st.file_uploader("프로젝트 파일 불러오기", type=["json"], key="proj_upload")
        if up is not None and st.button("불러와서 현재 작업 바꾸기", help="현재 세션의 작업이 불러온 파일 내용으로 바뀝니다."):
            try:
                ss.proj, notes = store.import_project(up.getvalue())
                ss.texts, ss.run = {}, None
                ss._import_notes = notes
                st.rerun()
            except store.ImportError_ as exc:
                st.error(str(exc))
    for note in ss.pop("_import_notes", []):
        st.info(note)
    with st.expander("이용 안내"):
        st.caption("AI는 논문 읽기를 돕는 초안을 만듭니다. 연구 결과나 본인의 의견을 대신 만들지 않으며, 제출 전 내용은 반드시 직접 확인해 주세요. "
                   "AI 기능을 쓰면 선택한 논문 본문이 OpenAI로 전송됩니다.")

st.title("대학원 AI 논문 연구 도우미")
st.caption("자료 찾기 → 논문 읽기 → 선행연구 분석 → 연구 방향·계획 → 연구 수행·분석 → 내 논문 작성")

menu = ss.menu
try:
    if menu.startswith("01"):
        from views import search as view
    elif menu.startswith("02"):
        from views import read as view
    elif menu.startswith("03"):
        from views import lit as view
    elif menu.startswith("04"):
        from views import plan as view
    elif menu.startswith("05"):
        from views import analysis as view
    elif menu.startswith("06"):
        from views import write as view
    else:
        from views import admin as view
    view.render()
except ImportError as exc:  # 한 메뉴의 의존성 문제가 다른 메뉴를 막지 않게 한다 (NFR-10)
    st.error(f"이 메뉴에 필요한 모듈을 불러오지 못했습니다: {exc.name}. 다른 메뉴는 계속 쓸 수 있습니다.")
