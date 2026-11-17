"""관리자: AI 품질평가 — 현황 / 데이터셋 / 실행 / 결과 검토 / 버전 비교·보고서 / 설정.

평가 대상은 이 서비스가 만든 AI 답변이다. 논문의 우수성, 학생 성적, 법적 적합성을 인증하지 않는다.
평가자료는 관리자 세션에만 있으며 서버에 영구 저장하지 않는다. 보관은 데이터셋 파일 내려받기로 한다.
"""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import hmac
import io
import json
import time
import uuid

import streamlit as st

from core import cite, evalq, exporters, pdfio
from views.common import proj, secret

RAGAS_METRICS = ["충실도(RAGAS)", "기준 답변 일치(RAGAS)"]


def _gate() -> str:
    """서버 쪽 Secrets의 비밀번호로만 연다. 설정이 없으면 메뉴를 열지 않는다 (EVAL-25)."""
    admin_pw, reviewer_pw = secret("ADMIN_PASSWORD"), secret("REVIEWER_PASSWORD")
    if not admin_pw:
        st.warning("관리자 비밀번호(Secrets의 ADMIN_PASSWORD)가 설정되지 않아 이 메뉴는 잠겨 있습니다. 연구 메뉴 01~06은 그대로 쓸 수 있습니다.")
        return ""
    role = st.session_state.get("role", "")
    if role:
        if st.button("관리자 메뉴 잠그기"):
            st.session_state.role = ""
            st.rerun()
        return role
    with st.form("gate"):
        pw = st.text_input("관리자 또는 검토자 비밀번호", type="password")
        if st.form_submit_button("들어가기"):
            if hmac.compare_digest(pw.encode(), admin_pw.encode()):
                st.session_state.role = "관리자"
            elif reviewer_pw and hmac.compare_digest(pw.encode(), reviewer_pw.encode()):
                st.session_state.role = "검토자"
            else:
                time.sleep(1.0)
                st.error("비밀번호가 맞지 않습니다.")
            if st.session_state.get("role"):
                st.rerun()
    return ""


def _status(ev):
    runs, cases = ev["runs"], ev["cases"]
    c = st.columns(4)
    c[0].metric("평가 사례", len(cases))
    c[1].metric("실행", len(runs))
    c[2].metric("오류·부분 완료 실행", sum(1 for r in runs if r["state"] in ("오류", "부분 완료")))
    reviewed = {(r["run_id"], r["case_id"]) for r in ev["reviews"]}
    need = sum(1 for r in runs for res in r.get("results", []) if any(isinstance(v, dict) and v["state"] == "검토 필요" for v in res.values())
               and (r["run_id"], res["case_id"]) not in reviewed)
    c[3].metric("사람 검토가 필요한 사례", need)
    ok, ver = evalq.ragas_available()
    st.caption(f"RAGAS: {'사용 가능 · 버전 ' + ver if ok else '사용 불가 (' + ver + ') — 규칙 검사만 가능'} · 기준 답변이 검토 완료된 사례 "
               f"{sum(1 for x in cases if x['review_state'] == '검토 완료')}건")
    if runs:
        st.dataframe([{"실행": r["run_id"], "시작": r["started"], "상태": r["state"], "사례": len(r["conditions"]["case_ids"]),
                       "지표": ", ".join(r["conditions"]["metrics"]), "평가모델": r["conditions"]["eval_model"]} for r in reversed(runs)],
                     hide_index=True, width="stretch")
    else:
        st.info("아직 실행한 평가가 없습니다. ‘② 평가 데이터셋’에서 사례를 등록한 뒤 ‘③ 평가 실행’으로 가세요.")


def _dataset(ev):
    cases = ev["cases"]
    st.markdown("**이 세션의 AI 결과에서 가져오기**")
    gens = [g for g in proj()["generations"] if g["task"] in ("summary6", "summary7", "method") and g["generation_id"] not in {c.get("source_version") for c in cases}]
    if not gens:
        st.caption("가져올 AI 결과가 없습니다. 02 메뉴에서 요약을 만들면 여기에 나타납니다.")
    else:
        pick = st.multiselect("가져올 결과", range(len(gens)), format_func=lambda i: f"{gens[i]['task']} · {gens[i]['created']} · {gens[i].get('paper_id', '')}")
        consent = st.checkbox("이 AI 결과와 생성 당시 전달한 원문을 평가자료로 쓰는 데 동의합니다 (본인 자료에 한함)")
        if st.button("평가 사례로 등록", disabled=not (pick and consent)):
            for i in pick:
                g = gens[i]
                pid = g.get("paper_id", "")
                pages = st.session_state.texts.get(pid)
                ctx = []
                if pages and g.get("context", {}).get("kind") == "PDF 본문":
                    ctx = [pdfio.build_payload(pages)[0]]
                cases.append(evalq.new_case(paper_id=pid, citation=cite.format_citation(proj()["papers"][pid]) if pid in proj()["papers"] else "",
                                            source_version=g["generation_id"], task_type=g["task"], user_input=g["task"], response=g["data"],
                                            provided_contexts=ctx, consent_scope="본인 동의 · 이 세션 평가"))
            st.rerun()
    st.markdown("**파일로 가져오기** (JSON 목록 또는 CSV · 필수 열: response)")
    up = st.file_uploader("평가자료 파일", type=["json", "csv"], key="eval_up")
    if up is not None and st.button("파일의 사례 추가"):
        try:
            if up.size > 10 * 1024 * 1024:
                raise ValueError("파일이 너무 큽니다 (최대 10MB).")
            if up.name.lower().endswith(".json"):
                rows = json.loads(up.getvalue().decode("utf-8"))
                rows = rows.get("cases", rows) if isinstance(rows, dict) else rows
            else:
                import pandas as pd
                rows = pd.read_csv(io.BytesIO(up.getvalue())).fillna("").to_dict("records")
            ok, problems = evalq.validate_cases(rows)
            cases.extend(ok)
            st.success(f"{len(ok)}건을 추가했습니다." + (f" 제외 {len(problems)}건: {problems[:3]}" if problems else ""))
        except Exception as exc:
            st.error(f"파일을 읽지 못했습니다: {exc}")
    with st.expander("직접 입력"):
        with st.form("case_new"):
            resp = st.text_area("평가할 AI 답변")
            ctx = st.text_area("생성 당시 AI에 전달한 원문 근거")
            exp = st.text_input("기대 동작 (예: 원문에 없으므로 ‘확인되지 않음’이라고 답해야 함)")
            if st.form_submit_button("추가") and resp.strip():
                cases.append(evalq.new_case(task_type="직접 입력", response=resp, provided_contexts=[ctx] if ctx.strip() else [], expected_behavior=exp))
                st.rerun()
    if not cases:
        return
    st.markdown(f"**등록된 사례 {len(cases)}건**")
    st.dataframe([{"case_id": c["case_id"], "유형": c["task_type"], "논문": c["citation"][:60], "전달 근거": "있음" if c["provided_contexts"] else "없음",
                   "기준 답변": c["review_state"] if c["reference"] else "없음", "분할": c["split"], "동의 범위": c["consent_scope"]} for c in cases],
                 hide_index=True, width="stretch")
    cid = st.selectbox("기준 답변·필수 내용을 편집할 사례", [c["case_id"] for c in cases])
    case = next(c for c in cases if c["case_id"] == cid)
    with st.form(f"case_edit_{cid}"):
        ref = st.text_area("기준 답변 (사람이 원문을 보고 확인한 답)", str(case["reference"]))
        req = st.text_area("필수 내용 (한 줄에 하나)", "\n".join(case["required_points"]))
        c = st.columns(3)
        state = c[0].selectbox("기준 답변 검토 상태", ["검토 필요", "검토 완료"], index=1 if case["review_state"] == "검토 완료" else 0)
        reviewer = c[1].text_input("검토자", case["reviewer"])
        split = c[2].selectbox("분할", ["개발용", "검증용"], index=0 if case["split"] == "개발용" else 1)
        s1, s2 = st.columns(2)
        if s1.form_submit_button("저장"):
            case.update({"reference": ref.strip(), "required_points": [x.strip() for x in req.split("\n") if x.strip()],
                         "review_state": state if ref.strip() else "검토 필요", "reviewer": reviewer, "split": split})
            st.rerun()
        if s2.form_submit_button("이 사례 삭제"):
            cases.remove(case)
            st.rerun()
    st.caption("AI가 쓴 기준 답변은 사람이 확인하기 전까지 ‘검토 필요’입니다. ‘검토 완료’가 아니면 기준 답변이 필요한 지표는 실행되지 않습니다.")
    st.download_button("데이터셋 파일(JSON) 내려받기", json.dumps({"dataset_version": "v1", "cases": cases}, ensure_ascii=False, indent=1).encode("utf-8"),
                       f"eval_dataset_{exporters.stamp()}.json", help="전달 근거(논문 본문)가 들어 있습니다. 동의 범위 안에서만 보관하세요.")


def _run(ev):
    cases = ev["cases"]
    if not cases:
        st.info("먼저 ‘② 평가 데이터셋’에서 사례를 등록해 주세요.")
        return
    s = ev["settings"]
    pick = st.multiselect("평가할 사례", [c["case_id"] for c in cases], default=[c["case_id"] for c in cases][: s["max_cases"]], max_selections=s["max_cases"])
    metrics = st.multiselect("RAGAS 지표 (선택 · OpenAI 비용 발생)", RAGAS_METRICS, default=[])
    st.caption("규칙 검사(형식 준수, 근거 문구 대조, 수치 근거, 필수 내용 포함)는 항상 함께 실행하며 비용이 들지 않습니다. "
               "검색 문맥 정밀도·재현율과 Agent 평가는 제공하지 않습니다. 이 앱의 요약은 문단 검색(RAG)이나 도구 호출을 쓰지 않기 때문입니다.")
    chosen = [c for c in cases if c["case_id"] in pick]
    if metrics:
        ok, ver = evalq.ragas_available()
        st.dataframe(evalq.preflight(chosen, metrics), hide_index=True, width="stretch")
        st.warning(f"실행하면 선택한 사례의 AI 답변, 전달 근거, 기준 답변이 OpenAI({s['model']})로 전송됩니다. "
                   f"대상 {len(chosen)}건 × 지표 {len(metrics)}개이며 지표 하나가 모델을 여러 번 부릅니다. 최신 단가 정보가 없어 금액은 계산하지 않습니다.")
        if not ok:
            st.error("RAGAS를 불러올 수 없어 RAGAS 지표는 실행할 수 없습니다. 지표 선택을 비우면 규칙 검사만 실행됩니다.")
        if not st.session_state.get("openai_key"):
            st.info("RAGAS 지표를 실행하려면 왼쪽에 OpenAI API 키를 입력해 주세요.")
    blocked = not chosen or (metrics and (not st.session_state.get("openai_key") or not evalq.ragas_available()[0]))
    if st.button("평가 실행", type="primary", disabled=bool(blocked)):
        if metrics:
            bar = st.progress(0.0, "RAGAS 평가 중")
            run = evalq.run_ragas(chosen, metrics, st.session_state.openai_key, s["model"], s["threshold"], lambda i, n: bar.progress(i / n))
            bar.empty()
        else:
            run = {"run_id": "run_" + uuid.uuid4().hex[:8], "started": time.strftime("%Y-%m-%d %H:%M:%S"), "state": "완료", "results": [],
                   "conditions": {"metrics": [], "eval_model": "없음(규칙 검사만)", "threshold": s["threshold"], "ragas": "미사용",
                                  "dataset_versions": sorted({c["dataset_version"] for c in chosen}), "case_ids": [c["case_id"] for c in chosen]}}
        run["rules"] = {c["case_id"]: evalq.rule_checks(c, st.session_state.texts.get(c["paper_id"])) for c in chosen}
        ev["runs"].append(run)
        st.session_state.last_run = run["run_id"]
        st.rerun()
    if st.session_state.get("last_run"):
        run = next((r for r in ev["runs"] if r["run_id"] == st.session_state.last_run), None)
        if run:
            (st.success if run["state"] == "완료" else st.warning)(f"실행 {run['run_id']} · 상태: {run['state']}" + (f" · {run.get('error')}" if run.get("error") else ""))
            if run["state"] != "완료":
                st.caption("평가 요청이 실패한 사례는 ‘미평가’입니다. 답변 품질이 0점이라는 뜻이 아니며 같은 사례를 다시 실행할 수 있습니다.")


def _cell(v):
    if not isinstance(v, dict):
        return v
    return f"{v['state']}" + (f" ({v['score']})" if v.get("score") is not None else "") + (f" · {v['detail']}" if v.get("detail") else "")


def run_rows(run) -> list[dict]:
    by = {r["case_id"]: r for r in run.get("results", [])}
    rows = []
    for cid in run["conditions"]["case_ids"]:
        row = {"case_id": cid}
        row.update({k: _cell(v) for k, v in run.get("rules", {}).get(cid, {}).items()})
        row.update({k: _cell(v) for k, v in by.get(cid, {}).items() if k != "case_id"})
        rows.append(row)
    return rows


def _review(ev):
    if not ev["runs"]:
        st.info("검토할 실행이 없습니다.")
        return
    rid = st.selectbox("실행", [r["run_id"] for r in reversed(ev["runs"])])
    run = next(r for r in ev["runs"] if r["run_id"] == rid)
    st.dataframe(run_rows(run), hide_index=True, width="stretch")
    cid = st.selectbox("검토할 사례", run["conditions"]["case_ids"])
    case = next((c for c in ev["cases"] if c["case_id"] == cid), None)
    if not case:
        st.warning("이 사례는 데이터셋에서 삭제되었습니다.")
        return
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**AI 답변**")
        with st.container(border=True, height=260):
            st.write(evalq.response_text(case))
        st.markdown("**기준 답변·필수 내용**")
        st.write(case["reference"] or "기준 답변 없음")
        st.write("필수 내용: " + (" / ".join(case["required_points"]) or "등록 안 됨"))
    with c2:
        st.markdown("**생성 당시 전달한 원문 근거**")
        with st.container(border=True, height=260):
            st.write("\n\n".join(case["provided_contexts"])[:6000] or "전달 근거가 저장되어 있지 않습니다.")
        st.markdown("**자동 판정**")
        st.dataframe([{"검사": k, "판정": _cell(v)} for k, v in {**run.get("rules", {}).get(cid, {}),
                      **{k: v for r in run.get("results", []) if r["case_id"] == cid for k, v in r.items() if k != "case_id"}}.items()],
                     hide_index=True, width="stretch")
    with st.form(f"rev_{rid}_{cid}"):
        c = st.columns(3)
        verdict = c[0].selectbox("사람 검토", evalq.REVIEW_STATES[1:])
        target = c[1].selectbox("문제가 있는 곳", ["문제 없음", "AI 답변", "자동 판정", "둘 다"])
        who = c[2].text_input("검토자")
        comment = st.text_area("의견")
        if st.form_submit_button("검토 저장"):
            ev["reviews"].append({"review_id": "rev_" + uuid.uuid4().hex[:6], "run_id": rid, "case_id": cid, "verdict": verdict, "target": target,
                                  "reviewer": who, "comment": comment, "at": time.strftime("%Y-%m-%d %H:%M")})
            st.rerun()
    mine = [r for r in ev["reviews"] if r["run_id"] == rid and r["case_id"] == cid]
    if mine:
        st.dataframe(mine, hide_index=True, width="stretch")
    st.caption("사람 검토는 자동 점수를 바꾸지 않고 따로 기록합니다.")


def _report(ev):
    runs = ev["runs"]
    if not runs:
        st.info("보고할 실행이 없습니다.")
        return
    ids = [r["run_id"] for r in runs]
    rid = st.selectbox("보고서로 볼 실행", list(reversed(ids)), key="rep_run")
    run = next(r for r in runs if r["run_id"] == rid)
    st.json(run["conditions"], expanded=False)
    summary = evalq.summarize_run(run)
    if summary:
        st.dataframe(summary, hide_index=True, width="stretch")
    rows = run_rows(run)
    reviews = [r for r in ev["reviews"] if r["run_id"] == rid]
    st.caption(f"사람이 검토한 사례 {len({r['case_id'] for r in reviews})}/{len(rows)}건. 여러 지표를 합친 총점은 만들지 않습니다. 점수는 법적 인증이나 학술적 보증이 아닙니다.")
    c1, c2 = st.columns(2)
    c1.download_button("결과표 CSV", exporters.to_csv(rows), f"eval_{rid}.csv", "text/csv", width="stretch")
    c2.download_button("결과표 Excel", exporters.to_xlsx({"사례별 결과": rows, "지표 요약": summary, "사람 검토": reviews,
                                                        "실행 조건": [{"항목": k, "값": str(v)} for k, v in run["conditions"].items()]}),
                       f"eval_{rid}.xlsx", width="stretch")
    if len(runs) >= 2:
        st.markdown("**개선 전후 비교**")
        c = st.columns(2)
        a = c[0].selectbox("이전 실행", ids, index=len(ids) - 2)
        b = c[1].selectbox("이후 실행", ids, index=len(ids) - 1)
        cmp_ = evalq.compare_runs(next(r for r in runs if r["run_id"] == a), next(r for r in runs if r["run_id"] == b))
        if not cmp_["same_conditions"]:
            st.warning(f"동일 조건 비교가 아닙니다. 달라진 항목: {cmp_['different']}")
        if cmp_["rows"]:
            st.dataframe(cmp_["rows"], hide_index=True, width="stretch")
        else:
            st.caption("두 실행에 공통으로 점수가 있는 사례·지표가 없습니다.")


def _settings(ev):
    s = ev["settings"]
    s["threshold"] = st.slider("‘기준 충족’으로 볼 점수 기준", 0.0, 1.0, float(s["threshold"]), 0.05,
                               help="임시 기준입니다. 사람이 검토한 사례와 맞춰 본 뒤 정하세요.")
    s["max_cases"] = st.number_input("한 번에 실행할 최대 사례 수", 1, 100, int(s["max_cases"]))
    s["model"] = st.text_input("평가 모델", s["model"], help="답변을 만든 모델과 평가 모델이 같으면 판정이 후해질 수 있습니다.")
    ok, ver = evalq.ragas_available()
    st.caption(f"RAGAS {'버전 ' + ver if ok else '사용 불가'} · 비밀 키는 실행 이력에 저장하지 않습니다 · 평가자료와 이력은 이 세션에만 있습니다.")


def render():
    st.header("관리자: AI 품질평가")
    st.caption("이 서비스가 만든 AI 답변의 품질을 점검하는 메뉴입니다. 논문이나 사람을 평가하지 않습니다.")
    role = _gate()
    if not role:
        return
    ev = proj()["eval"]
    names = ["① 평가 현황", "② 평가 데이터셋", "③ 평가 실행", "④ 결과 검토", "⑤ 버전 비교·보고서", "⑥ 평가 설정"]
    if role == "검토자":
        st.info("검토자 권한입니다. 결과 검토만 할 수 있습니다.")
        _review(ev)
        return
    tabs = st.tabs(names)
    for tab, fn in zip(tabs, (_status, _dataset, _run, _review, _report, _settings)):
        with tab:
            fn(ev)
