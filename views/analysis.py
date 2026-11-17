"""05. 연구 수행·분석 — 실험 기록, 데이터 점검, 결과표·그래프, 재현 정보.

여기는 사용자의 실제 연구 데이터를 다룬다. 관리자 메뉴의 ‘AI 품질평가’와는 다른 기능이다.
"""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import hashlib
import io
import time
import uuid

import streamlit as st

from core import exporters, store
from views.common import proj

AGGS = {"건수": "count", "합계": "sum", "평균": "mean", "중앙값": "median", "최소": "min", "최대": "max"}


def _load(up):
    import pandas as pd

    raw = up.getvalue()
    if len(raw) > 30 * 1024 * 1024:
        raise ValueError("파일이 너무 큽니다 (최대 30MB).")
    if up.name.lower().endswith(".csv"):
        for enc in ("utf-8-sig", "cp949"):
            try:
                return pd.read_csv(io.BytesIO(raw), encoding=enc), raw
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV 글자 인코딩을 읽지 못했습니다 (UTF-8 또는 CP949만 지원).")
    return pd.read_excel(io.BytesIO(raw)), raw


def render():
    import pandas as pd

    st.header("05. 연구 수행·분석")
    st.caption("본인이 실제로 수집·실험한 자료만 올리세요. 올린 자료는 이 세션에서만 쓰이며 AI나 공용 평가에 보내지 않습니다.")
    t1, t2, t3 = st.tabs(["데이터 점검·결과 만들기", "실험 기록", "등록한 결과"])
    with t1:
        up = st.file_uploader("연구 데이터 (CSV 또는 Excel)", type=["csv", "xlsx"])
        if up is None:
            st.info("데이터가 없으면 결과를 만들지 않습니다. 계획 단계라면 04 메뉴에서 RQ와 필요한 자료를 먼저 정리하세요.")
        else:
            try:
                df0, raw = _load(up)
            except Exception as exc:
                st.error(f"파일을 읽지 못했습니다: {exc}")
                return
            c = st.columns(2)
            source = c[0].text_input("자료 출처", key="ds_source")
            perm = c[1].text_input("사용 권한·동의 범위", key="ds_perm")
            unit = st.text_input("자료 단위·표본 수·제외 기준 메모", key="ds_unit")
            st.markdown("**① 원본 점검**")
            m = st.columns(4)
            m[0].metric("행", f"{len(df0):,}")
            m[1].metric("열", len(df0.columns))
            m[2].metric("결측이 있는 행", f"{int(df0.isna().any(axis=1).sum()):,}")
            m[3].metric("완전히 중복된 행", f"{int(df0.duplicated().sum()):,}")
            st.dataframe(pd.DataFrame({"자료형": df0.dtypes.astype(str), "결측 수": df0.isna().sum(), "고유값 수": df0.nunique()}), width="stretch")
            num = df0.select_dtypes("number")
            if not num.empty:
                st.dataframe(num.describe().T.round(3), width="stretch")
            st.markdown("**② 전처리** (원본은 바꾸지 않고 사본에만 적용합니다)")
            c = st.columns(2)
            drop_dup = c[0].checkbox("완전히 중복된 행 제거")
            drop_na = c[1].multiselect("이 열이 비어 있는 행 제거", list(df0.columns))
            df = df0.copy()
            steps = []
            if drop_dup:
                n = len(df)
                df = df.drop_duplicates()
                steps.append(f"중복 행 제거: {n:,} → {len(df):,}행")
            if drop_na:
                n = len(df)
                df = df.dropna(subset=drop_na)
                steps.append(f"결측 행 제거({', '.join(map(str, drop_na))}): {n:,} → {len(df):,}행")
            st.caption("적용한 전처리: " + ("; ".join(steps) if steps else "없음 (원본 그대로)"))
            st.markdown("**③ 결과표·그래프**")
            c = st.columns(3)
            group = c[0].selectbox("묶을 기준 열", [None] + list(df.columns), format_func=lambda x: "묶지 않음" if x is None else str(x))
            value = c[1].selectbox("값 열 (숫자)", list(df.select_dtypes("number").columns) or [None])
            agg = c[2].selectbox("계산", list(AGGS))
            result = None
            if value is None:
                st.warning("숫자 열이 없어 계산할 수 없습니다.")
            elif group is None:
                result = pd.DataFrame({f"{value} · {agg}": [df[value].agg(AGGS[agg])]})
            else:
                result = df.groupby(group, dropna=False)[value].agg(AGGS[agg]).reset_index(name=f"{value} · {agg}")
            if result is not None:
                st.dataframe(result.round(4), width="stretch", hide_index=True)
                unit_y = st.text_input("값의 단위 (그래프 세로축에 표시)", key="unit_y")
                if group is not None and len(result) <= 60:
                    st.bar_chart(result.set_index(group), x_label=str(group), y_label=f"{value} · {agg}" + (f" ({unit_y})" if unit_y else ""))
                rq = st.text_input("이 결과가 답하는 연구질문(RQ)")
                title = st.text_input("결과 이름 (예: 표 1. 엔진별 평균 내보내기 시간)")
                if st.button("결과로 등록", type="primary"):
                    if not title.strip():
                        st.error("결과 이름을 입력해 주세요.")
                    else:
                        proj()["experiments"].append({
                            "kind": "result", "id": "res_" + uuid.uuid4().hex[:6], "title": title.strip(), "rq": rq, "at": time.strftime("%Y-%m-%d %H:%M"),
                            "dataset": {"name": up.name, "sha256": hashlib.sha256(raw).hexdigest()[:16], "rows_original": len(df0), "rows_used": len(df),
                                        "source": source, "permission": perm, "unit_note": unit},
                            "preprocessing": steps, "spec": {"group": None if group is None else str(group), "value": str(value), "agg": agg, "unit": unit_y},
                            "table": result.round(6).astype(object).where(result.notna(), None).head(200).to_dict("records")})
                        store.log(proj(), f"결과 등록: {title}")
                        st.success("등록했습니다. 06 메뉴의 Results 작성에 연결됩니다.")
            st.caption("회귀·검정 같은 고급 통계, 모형 학습, 자동 실험 실행은 이 앱에서 지원하지 않습니다. 따로 분석한 뒤 결과 파일을 올려 주세요.")
    with t2:
        with st.form("exp"):
            c = st.columns(2)
            name = c[0].text_input("실험 이름")
            when = c[1].text_input("실행 시각", time.strftime("%Y-%m-%d %H:%M"))
            env = st.text_input("환경·도구와 버전 (예: Aurora MySQL 3.x, Python 3.12, pandas 2.2)")
            script = st.text_input("사용한 스크립트·명령 (파일 이름이나 저장소 위치)")
            cond = st.text_area("실험 조건", height=70)
            outcome = st.selectbox("결과", ["성공", "실패", "부분 성공", "재실행"])
            memo = st.text_area("관찰한 내용 (기대와 다른 결과도 그대로 적습니다)", height=70)
            if st.form_submit_button("기록 추가"):
                if name.strip():
                    proj()["experiments"].append({"kind": "log", "id": "exp_" + uuid.uuid4().hex[:6], "title": name.strip(), "at": when, "env": env,
                                                  "script": script, "conditions": cond, "outcome": outcome, "memo": memo})
                    st.rerun()
                else:
                    st.error("실험 이름을 입력해 주세요.")
        logs = [e for e in proj()["experiments"] if e["kind"] == "log"]
        if logs:
            st.dataframe([{"실험": e["title"], "시각": e["at"], "환경": e["env"], "결과": e["outcome"], "관찰": e["memo"]} for e in logs], hide_index=True, width="stretch")
    with t3:
        results = [e for e in proj()["experiments"] if e["kind"] == "result"]
        if not results:
            st.info("등록한 결과가 없습니다.")
        for e in results:
            with st.expander(f"{e['title']} · {e['at']}"):
                st.caption(f"RQ: {e['rq'] or '연결 안 됨'} · 자료 {e['dataset']['name']} ({e['dataset']['rows_used']:,}/{e['dataset']['rows_original']:,}행 사용) · "
                           f"전처리: {'; '.join(e['preprocessing']) or '없음'}")
                st.dataframe(e["table"], hide_index=True, width="stretch")
                if st.button("이 결과 삭제", key=f"del_{e['id']}"):
                    proj()["experiments"].remove(e)
                    st.rerun()
        if results:
            st.download_button("결과 전체 Excel 내려받기", exporters.to_xlsx({e["title"][:28]: e["table"] for e in results}), f"연구결과_{exporters.stamp()}.xlsx")
