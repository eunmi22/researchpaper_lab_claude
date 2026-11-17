"""AI 품질평가: 규칙 검사(무료·즉시) + RAGAS 충실도(선택·유료) + 사람 검토.

평가 대상은 이 서비스가 만든 AI 답변이다. 논문의 우수성이나 학생의 성적을 평가하지 않는다.
오류·미평가는 0점으로 바꾸지 않는다 (EVAL-16).
"""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import asyncio
import re
import time
import uuid

from .ai import SEVEN, SIX
from .pdfio import verify_quote
from .version import APP_VERSION, PROMPT_VERSION

CASE_FIELDS = ["case_id", "dataset_version", "paper_id", "citation", "source_version", "task_type", "user_input", "response",
               "provided_contexts", "reference", "required_points", "expected_behavior", "review_state", "reviewer",
               "consent_scope", "sensitivity", "split"]
RUN_STATES = ["대기", "실행 중", "완료", "부분 완료", "오류", "취소"]
EVAL_STATES = ["미평가", "기준 충족", "검토 필요", "기준 미달", "해당 없음"]
REVIEW_STATES = ["미검토", "동의", "내용 수정 필요", "자동 판정 이의", "판단 보류"]
FABRICATION = re.compile(r"\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*배")


def ragas_available() -> tuple[bool, str]:
    try:
        import ragas  # noqa: F401
        from ragas.metrics.collections import Faithfulness  # noqa: F401
        return True, getattr(ragas, "__version__", "?")
    except Exception as exc:  # 설치 안 됨·버전 불일치 모두 여기로
        return False, type(exc).__name__


def new_case(**kw) -> dict:
    case = {k: "" for k in CASE_FIELDS}
    case.update({"case_id": "case_" + uuid.uuid4().hex[:8], "dataset_version": "v1", "provided_contexts": [],
                 "required_points": [], "review_state": "검토 필요", "split": "개발용", "consent_scope": "본인 평가만"})
    case.update(kw)
    return case


def validate_cases(rows) -> tuple[list[dict], list[str]]:
    """업로드 평가자료의 구조 검사 (EVAL-04)."""
    ok, problems = [], []
    if not isinstance(rows, list):
        return [], ["목록 형식이 아닙니다."]
    for i, row in enumerate(rows[:500], start=1):
        if not isinstance(row, dict) or not str(row.get("response", "")).strip():
            problems.append(f"{i}번째: response(평가할 AI 답변)가 없습니다.")
            continue
        case = new_case()
        for k in CASE_FIELDS:
            if k in row and row[k] not in (None, ""):
                case[k] = row[k]
        for k in ("provided_contexts", "required_points"):
            if isinstance(case[k], str):
                case[k] = [s.strip() for s in re.split(r"\n\s*\n|\|\|", case[k]) if s.strip()]
        case["review_state"] = case["review_state"] if case["reference"] and row.get("review_state") == "검토 완료" else "검토 필요"
        ok.append(case)
    return ok, problems


def format_check(task: str, data: dict) -> dict:
    """모드별 필수 항목과 한 문장 여부 (형식 준수). 내용의 진위는 보지 않는다."""
    want = SIX if task == "summary6" else SEVEN if task == "summary7" else []
    if not want:
        return {"state": "해당 없음", "detail": "형식 기준이 없는 유형"}
    items = (data or {}).get("items", {})
    missing = [k for k in want if not str((items.get(k) or {}).get("text", "")).strip()]
    extra = [k for k in items if k not in want]
    long_ = [k for k in want if len(re.findall(r"[.?!。](?:\s|$)", str((items.get(k) or {}).get("text", "")))) > 1]
    if missing or extra:
        return {"state": "기준 미달", "detail": f"누락 {missing} / 불필요 {extra}"}
    if long_:
        return {"state": "검토 필요", "detail": f"한 문장을 넘는 항목: {long_}"}
    return {"state": "기준 충족", "detail": f"{len(want)}개 항목 모두 있음"}


def evidence_check(data: dict, pages: list[str] | None) -> dict:
    """근거 문구가 원문 추출 텍스트에 있는지 대조. 원문이 없으면 미평가."""
    entries = list(((data or {}).get("items") or {}).items()) + list(((data or {}).get("sections") or {}).items())
    if not entries:
        return {"state": "해당 없음", "detail": "근거가 붙는 항목 없음", "rows": []}
    if not pages:
        return {"state": "미평가", "detail": "원문 PDF가 세션에 없어 대조하지 못함", "rows": []}
    rows = []
    for name, item in entries:
        item = item or {}
        page = (item.get("pages") or [None])[0]
        rows.append({"항목": name, "쪽": page, "대조": verify_quote(pages, page, item.get("quote", ""))})
    bad = [r for r in rows if r["대조"] in ("불일치", "문구 없음")]
    state = "기준 충족" if not bad else "검토 필요"
    return {"state": state, "detail": f"{len(rows) - len(bad)}/{len(rows)}개 근거 문구가 원문과 일치", "rows": rows}


def number_check(response_text: str, contexts: list[str]) -> dict:
    """답변에 나온 %·배수 수치가 제공 근거에 있는지 (허위 수치 후보 탐지)."""
    if not contexts:
        return {"state": "미평가", "detail": "제공 근거 없음"}
    ctx = re.sub(r"\s", "", " ".join(contexts))
    nums = {re.sub(r"\s", "", m) for m in FABRICATION.findall(response_text or "")}
    absent = sorted(n for n in nums if n not in ctx)
    if absent:
        return {"state": "검토 필요", "detail": f"근거에서 찾지 못한 수치: {absent}"}
    return {"state": "기준 충족", "detail": "수치 없음" if not nums else "모든 수치가 근거에 있음"}


def required_check(response_text: str, required: list[str]) -> dict:
    if not required:
        return {"state": "미평가", "detail": "필수 내용(required_points)이 등록되지 않음"}
    flat = re.sub(r"\s", "", response_text or "")
    miss = [p for p in required if re.sub(r"\s", "", p) not in flat]
    return {"state": "기준 충족" if not miss else "검토 필요", "detail": f"누락 {miss}" if miss else "필수 내용 모두 포함"}


def response_text(case: dict) -> str:
    r = case.get("response")
    if isinstance(r, dict):
        parts = [str((v or {}).get("text", "")) for v in list((r.get("items") or {}).values()) + list((r.get("sections") or {}).values())]
        parts += [str(v) for v in (r.get("three_lines") or {}).values()]
        return " ".join(p for p in parts if p)
    return str(r or "")


def rule_checks(case: dict, pages: list[str] | None = None) -> dict:
    data = case["response"] if isinstance(case.get("response"), dict) else {}
    text = response_text(case)
    return {"형식 준수": format_check(case.get("task_type", ""), data), "근거 문구 대조": evidence_check(data, pages),
            "수치 근거": number_check(text, case.get("provided_contexts") or []),
            "필수 내용 포함": required_check(text, case.get("required_points") or [])}


def preflight(cases: list[dict], metrics: list[str]) -> list[dict]:
    """실행 전 입력 부족 안내 (EVAL-10, EVAL-14). 부족한 근거를 만들어 채우지 않는다."""
    out = []
    for c in cases:
        row = {"case_id": c["case_id"]}
        if "충실도(RAGAS)" in metrics:
            row["충실도(RAGAS)"] = "실행 가능" if c.get("provided_contexts") else "실행 불가: 생성 당시 전달 근거 없음"
        if "기준 답변 일치(RAGAS)" in metrics:
            okref = c.get("reference") and c.get("review_state") == "검토 완료"
            row["기준 답변 일치(RAGAS)"] = "실행 가능" if okref else "실행 불가: 사람이 검토한 기준 답변 없음"
        out.append(row)
    return out


def run_ragas(cases: list[dict], metrics: list[str], api_key: str, model: str, threshold: float, progress=None) -> dict:
    """저장된 답변을 RAGAS로 평가. 사례별 실패는 '오류'로 남기고 점수를 만들지 않는다."""
    ok, ver = ragas_available()
    run = {"run_id": "run_" + uuid.uuid4().hex[:8], "started": time.strftime("%Y-%m-%d %H:%M:%S"), "state": "실행 중",
           "conditions": {"metrics": metrics, "eval_model": model, "threshold": threshold, "ragas": ver, "app": APP_VERSION,
                          "prompt": PROMPT_VERSION, "dataset_versions": sorted({c.get("dataset_version", "") for c in cases}),
                          "case_ids": [c["case_id"] for c in cases]}, "results": []}
    if not ok:
        run["state"], run["error"] = "오류", "RAGAS를 불러올 수 없습니다. 규칙 검사는 계속 쓸 수 있습니다."
        return run
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import FactualCorrectness, Faithfulness

    llm = llm_factory(model, client=AsyncOpenAI(api_key=api_key, timeout=120, max_retries=1))
    plan = preflight(cases, metrics)

    async def one(case, row):
        res = {"case_id": case["case_id"]}
        text = response_text(case)
        for metric in metrics:
            if not str(row.get(metric, "")).startswith("실행 가능"):
                res[metric] = {"state": "해당 없음", "score": None, "detail": row.get(metric, "")}
                continue
            try:
                if metric == "충실도(RAGAS)":
                    r = await Faithfulness(llm=llm).ascore(user_input=case.get("user_input") or "논문 요약", response=text,
                                                         retrieved_contexts=[c[:12000] for c in case["provided_contexts"]][:8])
                else:
                    r = await FactualCorrectness(llm=llm).ascore(response=text, reference=str(case["reference"]))
                score = float(r.value)
                res[metric] = {"state": "기준 충족" if score >= threshold else "검토 필요", "score": round(score, 3), "detail": ""}
            except Exception as exc:
                res[metric] = {"state": "미평가", "score": None, "detail": f"오류: {type(exc).__name__}"}
        return res

    async def all_():
        out = []
        for i, (case, row) in enumerate(zip(cases, plan), start=1):
            out.append(await one(case, row))
            if progress:
                progress(i, len(cases))
        return out

    try:
        run["results"] = asyncio.run(all_())
    except Exception as exc:
        run["state"], run["error"] = "오류", f"실행 실패: {type(exc).__name__}"
        return run
    cells = [v for r in run["results"] for k, v in r.items() if k != "case_id"]
    errors = sum(1 for v in cells if str(v["detail"]).startswith("오류"))
    valid = sum(1 for v in cells if v["score"] is not None)
    run["state"] = "오류" if cells and errors == len(cells) else "부분 완료" if errors else "완료"
    run["valid"], run["errors"] = valid, errors
    run["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return run


def summarize_run(run: dict) -> list[dict]:
    """지표별 유효 평가 수·제외·오류·평균. 유효 평가가 없으면 평균을 내지 않는다 (EVAL-22, EVAL-23)."""
    rows = []
    for metric in run["conditions"]["metrics"]:
        cells = [r[metric] for r in run.get("results", []) if metric in r]
        scores = [c["score"] for c in cells if c["score"] is not None]
        rows.append({"지표": metric, "대상": len(cells), "유효 평가": len(scores),
                     "해당 없음": sum(1 for c in cells if c["state"] == "해당 없음"),
                     "오류": sum(1 for c in cells if str(c["detail"]).startswith("오류")),
                     "평균": round(sum(scores) / len(scores), 3) if scores else "유효 평가 없음"})
    return rows


def compare_runs(a: dict, b: dict) -> dict:
    """같은 조건인지 먼저 밝히고 사례별 개선·악화를 센다 (EVAL-21)."""
    diff = [k for k in ("metrics", "eval_model", "threshold", "ragas", "dataset_versions", "prompt")
            if a["conditions"].get(k) != b["conditions"].get(k)]
    ra = {r["case_id"]: r for r in a.get("results", [])}
    rows = []
    for r in b.get("results", []):
        base = ra.get(r["case_id"])
        if not base:
            continue
        for metric, cell in r.items():
            if metric == "case_id" or metric not in base:
                continue
            s0, s1 = base[metric]["score"], cell["score"]
            change = "비교 불가" if s0 is None or s1 is None else "개선" if s1 > s0 else "악화" if s1 < s0 else "동일"
            rows.append({"case_id": r["case_id"], "지표": metric, "이전": s0, "이후": s1, "변화": change})
    return {"same_conditions": not diff, "different": diff, "rows": rows}
