"""A4 보고서 양식: 항목 켜기·끄기, 순서, 제목. 모든 논문에 함께 적용한다 (프로젝트 설정)."""
from __future__ import annotations

FILE_VERSION = "3.2.0"

SEC_DEF = {"cite": "서지정보", "reason": "선정 이유", "overview": "연구 개요", "tech": "활용 기술·분석 대상",
           "three": "연구 기여도와 증명방법 (핵심 3줄)", "limits": "한계", "s7": "논문 요약 (7문장)",
           "replication": "따라 해 보기 검토", "thoughts": "나의 생각·의문"}
PRESETS = {"기본": ["cite", "reason", "overview", "tech", "three", "limits", "thoughts"],
           "간단": ["cite", "three", "thoughts"],
           "상세": ["cite", "reason", "s7", "three", "limits", "replication", "thoughts"]}
PRESET_NAMES = list(PRESETS) + ["직접 구성"]
# A4 한 쪽은 대략 1,600~1,800자, 상세 양식은 두 쪽 기준
PRESET_LIMIT = {"기본": 1800, "간단": 1000, "상세": 3600, "직접 구성": 1800}


def preset_sections(name: str, prev: list | None = None) -> list[dict]:
    labels = {s["id"]: s.get("label") for s in (prev or [])}
    on = PRESETS[name]
    rest = [k for k in SEC_DEF if k not in on]
    return [{"id": k, "on": True, "label": labels.get(k) or SEC_DEF[k]} for k in on] + \
           [{"id": k, "on": False, "label": labels.get(k) or SEC_DEF[k]} for k in rest]


def default_tpl() -> dict:
    return {"title": "논문 요약 보고서", "preset": "기본", "sections": preset_sections("기본"), "_v": 0}


def valid(tpl) -> bool:
    return isinstance(tpl, dict) and isinstance(tpl.get("sections"), list) and \
        {s.get("id") for s in tpl["sections"] if isinstance(s, dict)} == set(SEC_DEF)
