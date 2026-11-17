"""세션 보관·프로젝트 내보내기/복원 (DATA-01~07). 영구 저장이 아니다: 브라우저를 닫으면 사라지므로 JSON으로 내려받는다."""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import copy
import json
import re
import time

from . import reporttpl

from .version import APP_VERSION, SCHEMA_VERSION

SECRET_PATTERNS = [re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), re.compile(r"(?i)(api[_-]?key|servicekey|authorization)\s*[=:]\s*\S{12,}")]


def new_project() -> dict:
    return {
        "schema_version": SCHEMA_VERSION, "app_version": APP_VERSION, "created": time.strftime("%Y-%m-%d %H:%M"),
        "project": {"topic": "", "purpose": "", "period": "", "resources": "", "advisor": "",
                    "cover": {"school": "", "major": "", "course": "", "name": "", "date": ""},
                    "report_tpl": reporttpl.default_tpl()},
        "papers": {},        # paper_id → 서지
        "docs": {},          # paper_id → 원문 메타(본문 텍스트는 내보내지 않음)
        "notes": {},         # paper_id → 읽기노트
        "generations": [],   # AI 실행 이력
        "comparison": {}, "ideas": [], "adopted": [], "plan": {}, "plan_log": [],
        "experiments": [], "manuscript": {}, "history": [],
        "design": {},        # 나의 연구 설계안 (장별 인터뷰·초안·초록)
        "eval": {"cases": [], "runs": [], "reviews": [], "settings": {"threshold": 0.8, "max_cases": 30, "model": "gpt-4.1-mini"}},
    }


def empty_note() -> dict:
    return {"abstract": "", "summary6": {}, "summary7": {}, "method": {}, "ai_questions": [], "edited": {},
            "my_thoughts": "", "selection_reason": "", "extra_limits": "", "read_state": "읽기 전", "source_scope": "",
            "page_notes": {}, "memos": {}, "chat": [], "questions3": {}, "tutor": {}, "drafts": {}}


def store_note_defaults() -> dict:
    return empty_note()


def redact(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub("[비밀값 제거]", text)
    return text


def export_project(proj: dict) -> bytes:
    """API 키·원문 본문은 넣지 않는다 (DATA-03, DATA-04). 원문은 식별정보만 남긴다."""
    data = copy.deepcopy(proj)
    for doc in data.get("docs", {}).values():
        doc.pop("pages", None)
        doc["pdf_included"] = False
    data["exported"] = time.strftime("%Y-%m-%d %H:%M")
    return redact(json.dumps(data, ensure_ascii=False, indent=1)).encode("utf-8")


class ImportError_(Exception):
    pass


def import_project(raw: bytes) -> tuple[dict, list[str]]:
    if len(raw) > 20 * 1024 * 1024:
        raise ImportError_("파일이 너무 큽니다.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportError_("프로젝트 JSON 형식이 아닙니다.") from exc
    if not isinstance(data, dict) or "schema_version" not in data or not isinstance(data.get("papers"), dict):
        raise ImportError_("이 앱에서 내보낸 프로젝트 파일이 아닙니다.")
    notes = []
    if data["schema_version"] > SCHEMA_VERSION:
        raise ImportError_("더 새 버전의 앱에서 만든 파일입니다. 앱을 최신으로 올린 뒤 다시 시도해 주세요.")
    base = new_project()
    for key, default in base.items():
        if key not in data or not isinstance(data[key], type(default)):
            data[key] = default
            notes.append(f"'{key}' 항목이 없어 빈 값으로 채웠습니다.")
    for k, v in base["eval"].items():
        data["eval"].setdefault(k, v)
    if not reporttpl.valid(data["project"].get("report_tpl")):
        data["project"]["report_tpl"] = reporttpl.default_tpl()
    for note in data["notes"].values():
        if isinstance(note, dict):
            for k, v in store_note_defaults().items():
                note.setdefault(k, v)
    if data.get("docs"):
        notes.append("원문 PDF는 프로젝트 파일에 들어 있지 않습니다. 02 메뉴에서 같은 PDF를 다시 올려 주세요.")
    data["schema_version"] = SCHEMA_VERSION
    return data, notes


def log(proj: dict, what: str) -> None:
    proj["history"].append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "what": what})
    del proj["history"][:-200]


def safe_cell(value):
    """CSV·Excel에서 수식으로 해석될 수 있는 문자열을 무력화 (NFR-07)."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
