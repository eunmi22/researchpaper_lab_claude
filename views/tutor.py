"""02-② 페이지별 읽기 — 원문 쪽을 보며 질문에 답하고, 2~3문장으로 직접 요약하는 튜터.

원칙: 완성된 답을 먼저 주지 않는다. 힌트는 단계별로, 모범 요약은 학생이 직접 요청할 때만 보여 준다.
끝낸 요약은 그 쪽 메모(쪽별 요약)와 보고서 ‘연구 개요’ 재료로 쓰인다.
"""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import streamlit as st

from core import pdfio, tutor
from views.common import need_key_notice, proj, run_prompt

STEP_NAMES = ["① 찾아볼 위치", "② 주목할 표현", "③ 빈칸 문장", "④ 빈칸을 채운 문장"]


@st.cache_data(max_entries=48, show_spinner=False)
def _page_png(doc_id: str, n: int, _data: bytes) -> bytes | None:
    try:
        import pymupdf
        with pymupdf.open(stream=_data, filetype="pdf") as d:
            return d[n - 1].get_pixmap(dpi=110).tobytes("png")
    except Exception:
        return None


def _state(note: dict, pages: list[str]) -> dict:
    T = note.get("tutor")
    if not isinstance(T, dict) or not T:
        T = note["tutor"] = {"mode": "seq", "cur": 1, "pages": {}, "key": None, "print": None}
    for k, v in (("mode", "seq"), ("cur", 1), ("pages", {}), ("key", None), ("print", None)):
        T.setdefault(k, v)
    if not T["print"]:
        T["print"] = tutor.detect_printed(pages)
    T["cur"] = min(max(1, int(T.get("cur") or 1)), len(pages))
    return T


def _page(T: dict, n: int) -> dict:
    ps = T["pages"].setdefault(str(n), tutor.new_page_state())
    for k, v in tutor.new_page_state().items():
        ps.setdefault(k, v)
    return ps


# ---------- 콜백 (위젯 값은 콜백 안에서만 바꾼다) ----------
def _go(T: dict, n: int) -> None:
    T["cur"] = n


def _hint_more(ps: dict, i: int) -> None:
    ps["hint_lv"][str(i)] = int(ps["hint_lv"].get(str(i), 0)) + 1


def _put_summary(key: str, ps: dict, text: str) -> None:
    st.session_state[key] = text
    ps["summary"] = text


def _set_offset(T: dict, offset) -> None:
    T["print"]["offset"], T["print"]["confirmed"] = int(offset), True


def _print_bar(T: dict, n: int, pid: str) -> None:
    pr = T["print"]
    if pr.get("confirmed"):
        return
    with st.expander("인쇄 쪽수 맞추기" + (" · 자동으로 찾은 값 확인 필요" if pr.get("auto") is not None else " · 찾지 못함")):
        if pr.get("auto") is not None:
            k = max(n, pr["auto"] + 1)
            st.write(f"쪽 위·아래의 숫자로 보면 **PDF {k}쪽 = 인쇄 {k - pr['auto']}쪽**입니다 ({pr['support']}개 쪽에서 같은 차이).")
            st.button("맞아요, 이대로 쓰기", key=f"tu_prok_{pid}", on_click=_set_offset, args=(T, pr["auto"]))
        else:
            st.caption("쪽 번호 숫자를 찾지 못해 ‘인쇄 쪽수 확인되지 않음’으로 표시합니다. 논문에 인쇄된 쪽수를 알면 직접 맞춰 주세요.")
        val = st.number_input(f"지금 보는 PDF {n}쪽에 인쇄된 쪽수", min_value=1, max_value=9999, value=max(1, n), key=f"tu_prval_{pid}_{n}")
        st.button("이 값으로 맞추기", key=f"tu_prset_{pid}_{n}", on_click=_set_offset, args=(T, n - int(val)))


def _nav(T: dict, pages: list[str], pid: str) -> None:
    n, total = T["cur"], len(pages)
    seq = [int(x["page"]) for x in (T.get("key") or {}).get("pages", [])] if T["mode"] == "key" and T.get("key") else list(range(1, total + 1))
    seq = [x for x in seq if 1 <= x <= total] or list(range(1, total + 1))
    pos = seq.index(n) if n in seq else 0
    c = st.columns([1, 2, 1], vertical_alignment="center")
    c[0].button("◀ 이전 쪽", key=f"tu_prev_{pid}", disabled=pos == 0, on_click=_go, args=(T, seq[max(0, pos - 1)]), width="stretch")
    done = sum(1 for v in T["pages"].values() if v.get("done"))
    c[1].markdown(f"<div style='text-align:center'><b>{tutor.page_label(T['print'], n)}</b><br><small>끝낸 쪽 {done}개 · {pos + 1}/{len(seq)}</small></div>",
                  unsafe_allow_html=True)
    c[2].button("다음 쪽 ▶", key=f"tu_next_{pid}", disabled=pos >= len(seq) - 1, on_click=_go, args=(T, seq[min(len(seq) - 1, pos + 1)]), width="stretch")


def _mode(T: dict, pages: list[str], pid: str) -> None:
    labels = {"seq": "처음부터 차례로", "key": "중요 페이지만"}
    k = f"tu_mode_{pid}"
    if k not in st.session_state:
        st.session_state[k] = labels[T["mode"]]
    pick = st.radio("읽는 방식", list(labels.values()), key=k, horizontal=True)
    T["mode"] = "key" if pick == labels["key"] else "seq"
    if T["mode"] != "key":
        return
    key = T.get("key")
    if not key:
        st.caption("AI가 연구 목적·핵심 개념·주요 논증·결론이 나오는 쪽을 골라 줍니다. 결론이나 정답은 미리 알려 주지 않습니다.")
        if st.button("중요 페이지 고르기", key=f"tu_key_{pid}", disabled=not st.session_state.get("openai_key")):
            d = run_prompt(tutor.TUTOR_SYSTEM, tutor.key_prompt(pages, T["print"]), "tutor_key", "읽을 쪽을 고르는 중…", pid)
            if d:
                got = []
                for x in d.get("pages") or []:
                    try:
                        p = int(x.get("page"))
                    except (TypeError, ValueError, AttributeError):
                        continue
                    if 1 <= p <= len(pages) and p not in [g["page"] for g in got]:
                        got.append({"page": p, "why": str(x.get("why") or "")})
                if got:
                    T["key"] = {"pages": sorted(got, key=lambda g: g["page"]), "skip": str(d.get("skip") or "")}
                    T["cur"] = T["key"]["pages"][0]["page"]
                    st.rerun()
                st.warning("읽을 쪽을 고르지 못했습니다. 다시 눌러 주세요.")
        return
    with st.expander(f"고른 쪽 {len(key['pages'])}개", expanded=False):
        for g in key["pages"]:
            c = st.columns([1, 4], vertical_alignment="center")
            c[0].button(f"PDF {g['page']}쪽", key=f"tu_kgo_{pid}_{g['page']}", on_click=_go, args=(T, g["page"]), width="stretch")
            c[1].caption(g["why"])
        if key.get("skip"):
            st.caption("생략: " + key["skip"])
        if st.button("다시 고르기", key=f"tu_krst_{pid}"):
            T["key"] = None
            st.rerun()


def _left(pid: str, n: int, pages: list[str], doc_id: str) -> None:
    data = st.session_state.get("pdfs", {}).get(pid)
    png = _page_png(doc_id, n, data) if data else None
    if png:
        st.image(png, width="stretch")
        with st.expander("이 쪽 글자로 보기"):
            st.text(pages[n - 1] or "(추출된 글자 없음)")
    else:
        if not data:
            st.caption("쪽 그림은 이 세션에서 PDF를 올렸을 때만 보입니다. 지금은 추출한 글자로 보여 줍니다.")
        st.text_area("원문", pages[n - 1] or "(추출된 글자 없음)", height=560, disabled=True, label_visibility="collapsed", key=f"tu_raw_{pid}_{n}")


def _questions(pid: str, n: int, ps: dict, ctx: str, label: str, ready: bool) -> None:
    if ps["questions"] is None:
        st.caption("먼저 이 쪽에서 무엇을 찾으며 읽을지 질문을 받아 보세요.")
        if st.button("이 쪽 질문 받기", key=f"tu_q_{pid}_{n}", type="primary", disabled=not ready):
            d = run_prompt(tutor.TUTOR_SYSTEM, tutor.q_prompt(ctx, label), "tutor_q", "질문을 만드는 중…", pid)
            if d:
                qs = tutor.norm_questions(d)
                if qs:
                    ps.update({"questions": qs, "range": str(d.get("range") or ""), "role": str(d.get("role") or ""),
                               "continues": str(d.get("continues") or ""), "answers": {}, "hint_lv": {}, "checks": None})
                    st.rerun()
                st.warning("질문을 만들지 못했습니다. 다시 눌러 주세요.")
        return
    if ps["role"] or ps["range"]:
        st.markdown(f"**이 쪽의 역할** · {ps['role']}" + (f"  \n**읽을 범위** · {ps['range']}" if ps["range"] else ""))
    if ps["continues"]:
        st.info("다음 쪽으로 이어짐 · " + ps["continues"])
    checks = ps.get("checks") or []
    for i, q in enumerate(ps["questions"]):
        with st.container(border=True):
            st.markdown(f"**Q{i + 1}.** {q['q']}")
            if q.get("where"):
                st.caption("찾을 곳: " + q["where"])
            lv = int(ps["hint_lv"].get(str(i), 0))
            if lv >= 1 and q.get("hint"):
                st.markdown(f":orange-badge[힌트] {q['hint']}")
            steps = q.get("steps") or []
            for s in range(min(max(0, lv - 1), 4)):
                txt = steps[s] if s < len(steps) else ""
                if s == 3 and not txt.strip():
                    continue
                st.markdown(f":gray-badge[{STEP_NAMES[s]}] {txt}")
            if lv >= 5 and not (len(steps) > 3 and steps[3].strip()):
                frame = steps[2] if len(steps) > 2 else ""
                if ready and st.button("④ 빈칸을 채운 문장 보기", key=f"tu_h4_{pid}_{n}_{i}"):
                    d = run_prompt(tutor.TUTOR_SYSTEM, tutor.hint4_prompt(ctx, q["q"], frame), "tutor_hint4", "빈칸을 채우는 중…", pid)
                    if d and str(d.get("filled") or "").strip():
                        q["steps"] = (steps + [""] * 4)[:3] + [str(d["filled"]) + (f" ({d['note']})" if d.get("note") else "")]
                        st.rerun()
            if lv < 5:
                st.button("힌트 보기" if lv == 0 else "힌트 한 단계 더", key=f"tu_hint_{pid}_{n}_{i}", on_click=_hint_more, args=(ps, i))
            k = f"tu_ans_{pid}_{n}_{i}"
            if k not in st.session_state:
                st.session_state[k] = ps["answers"].get(str(i), "")
            st.text_area(f"Q{i + 1} 내 답", key=k, height=68, placeholder="원문에서 찾은 내용을 내 말로 적어 보세요")
            ps["answers"][str(i)] = st.session_state[k]
            if i < len(checks):
                c = checks[i]
                st.markdown(f":{tutor.VERDICT_COLOR.get(c.get('verdict'), 'gray')}-badge[{c.get('verdict', '')}] {c.get('comment', '')}")
    if st.button("내 답 확인받기", key=f"tu_chk_{pid}_{n}", disabled=not ready or not any(str(v).strip() for v in ps["answers"].values())):
        d = run_prompt(tutor.TUTOR_SYSTEM, tutor.check_prompt(ctx, ps["questions"], ps["answers"]), "tutor_check", "답을 확인하는 중…", pid)
        if d:
            ps["checks"] = [{"verdict": str(c.get("verdict") or ""), "comment": str(c.get("comment") or "")} for c in (d.get("checks") or []) if isinstance(c, dict)]
            st.rerun()


def _fill_view(pid: str, n: int, ps: dict, pages: list[str], sum_key: str) -> None:
    hf = ps.get("help_filled")
    if not hf:
        return
    with st.container(border=True):
        st.markdown("**원문에서 찾은 답**")
        for it in hf["items"]:
            v = pdfio.verify_quote(pages, n, it["quote"]) if it.get("quote") else ""
            badge = f" :{'green' if v == '일치' else 'orange' if v.startswith('다른') else 'red'}-badge[근거 {v}]" if v else ""
            st.markdown(f"- **{it['ask'] or '찾을 항목'}** → {it['answer']}{badge}")
            if it.get("quote"):
                st.caption(f"　근거: “{it['quote']}”" + (f" · {it['where']}" if it.get("where") else ""))
        if hf.get("filled"):
            st.markdown("**빈칸을 채운 틀**")
            st.text(hf["filled"])
        if hf.get("note"):
            st.caption("확인되지 않은 항목: " + hf["note"])
        if hf.get("summary"):
            st.markdown("**찾은 답으로 이은 요약**")
            st.write(hf["summary"])
            st.button("이 요약을 내 요약 칸에 넣기", key=f"tu_fuse_{pid}_{n}", on_click=_put_summary, args=(sum_key, ps, hf["summary"]),
                      help="넣은 뒤 내 말로 고쳐 쓰고 ‘요약 피드백 받기’를 눌러 보세요.")


def _feedback(fb: dict) -> None:
    for x in fb.get("correct", []):
        st.markdown(f":green-badge[정확] {x['text']}" + (f" :gray[{x['where']}]" if x.get("where") else ""))
    for x in fb.get("missing", []):
        tag = ":red-badge[꼭 보완]" if x.get("required") else ":blue-badge[더하면 좋음]"
        st.markdown(f"{tag} {x['text']}" + (f" :gray[{x['where']}]" if x.get("where") else ""))
    for x in fb.get("fix", []):
        st.markdown(f":orange-badge[고칠 곳] {x['text']}" + (f" — 힌트: {x['hint']}" if x.get("hint") else "") + (f" :gray[{x['where']}]" if x.get("where") else ""))
    if fb.get("typos"):
        st.caption("오탈자: " + ", ".join(fb["typos"]))


def _summary(pid: str, n: int, note: dict, ps: dict, ctx: str, label: str, pages: list[str], ready: bool) -> None:
    st.markdown("#### 이 쪽을 2~3문장으로 요약하기")
    k = f"tu_sum_{pid}_{n}"
    if k not in st.session_state:
        st.session_state[k] = ps.get("summary", "")
    st.text_area("내 요약", key=k, height=100, label_visibility="collapsed", placeholder="이 쪽의 핵심을 내 말로 2~3문장")
    ps["summary"] = st.session_state[k]
    c = st.columns(3)
    if c[0].button("요약 피드백 받기", key=f"tu_fb_{pid}_{n}", type="primary", disabled=not ready or not ps["summary"].strip()):
        d = run_prompt(tutor.TUTOR_SYSTEM, tutor.sumfb_prompt(ctx, label, ps["summary"]), "tutor_sumfb", "요약을 살펴보는 중…", pid)
        if d:
            ps["fb"], done = tutor.norm_feedback(d)
            if done:
                ps["done"], ps["done_summary"] = True, ps["summary"].strip()
                memos = note.setdefault("memos", {})
                memos[str(n)] = tutor.merge_memo(memos.get(str(n), ""), ps["done_summary"])
                st.session_state.pop(f"memo_{pid}_{n}", None)
            st.rerun()
    if c[1].button("요약 틀 도움", key=f"tu_help_{pid}_{n}", disabled=not ready):
        d = run_prompt(tutor.TUTOR_SYSTEM, tutor.help_prompt(ctx), "tutor_help", "요약 틀을 만드는 중…", pid)
        if d and str(d.get("frame") or "").strip():
            ps["help"], ps["help_filled"] = str(d["frame"]), None
            st.rerun()
    if c[2].button("모범 요약 보기", key=f"tu_model_{pid}_{n}", disabled=not ready, help="직접 써 본 뒤 비교용으로 보세요. 내 요약 칸에 자동으로 넣지 않습니다."):
        d = run_prompt(tutor.TUTOR_SYSTEM, tutor.model_prompt(ctx), "tutor_model", "모범 요약을 쓰는 중…", pid)
        if d and str(d.get("text") or "").strip():
            ps["model"] = str(d["text"])
            st.rerun()
    if ps.get("help"):
        with st.container(border=True):
            st.markdown("**요약 틀**")
            st.text(ps["help"])
            if not ps.get("help_filled") and st.button("틀 채우기 (원문에서 답 찾아 보여 주기)", key=f"tu_fill_{pid}_{n}", disabled=not ready):
                d = run_prompt(tutor.TUTOR_SYSTEM, tutor.fill_prompt(ctx, ps["help"]), "tutor_fill", "원문에서 답을 찾는 중…", pid)
                filled = tutor.norm_fill(d or {})
                if filled:
                    ps["help_filled"] = filled
                    st.rerun()
                elif d is not None:
                    st.warning("채울 답을 찾지 못했습니다. 다시 눌러 주세요.")
        _fill_view(pid, n, ps, pages, k)
    if ps.get("model"):
        with st.expander("모범 요약 (비교용)", expanded=True):
            st.write(ps["model"])
    if ps.get("fb"):
        with st.container(border=True):
            _feedback(ps["fb"])
            if ps.get("done"):
                st.success("이 쪽 요약을 끝냈습니다. ‘쪽별 요약’의 이 쪽 메모에 넣었고, 보고서 ④의 ‘연구 개요 만들기’ 재료로 쓰입니다.")
            else:
                st.caption("꼭 보완할 점과 고칠 곳을 반영한 뒤 다시 피드백을 받아 보세요.")


def _terms(pid: str, n: int, ps: dict, ctx: str, ready: bool) -> None:
    with st.expander("모르는 용어 물어보기", expanded=bool(ps["terms"])):
        with st.form(f"tu_tf_{pid}_{n}", clear_on_submit=True):
            term = st.text_input("용어", placeholder="예: 스냅숏")
            ok = st.form_submit_button("설명 보기", disabled=not ready)
        if ok and term.strip():
            d = run_prompt(tutor.TUTOR_SYSTEM, tutor.term_prompt(ctx, term.strip()), "tutor_term", "용어를 설명하는 중…", pid)
            if d:
                ps["terms"] = ([{"term": term.strip(), "explain": str(d.get("explain") or ""), "example": str(d.get("example") or ""),
                                 "from_paper": bool(d.get("from_paper")), "paper_case": str(d.get("paper_case") or "")}] + ps["terms"])[:8]
                st.rerun()
        for t in ps["terms"]:
            st.markdown(f"**{t['term']}** · {t['explain']}")
            if t.get("example"):
                st.caption(("예시(논문): " if t.get("from_paper") else "예시(설명용): ") + t["example"])
            if t.get("paper_case"):
                st.caption("이 논문에서는: " + t["paper_case"])


def render_tab(pid: str, note: dict) -> None:
    pages = st.session_state.texts.get(pid)
    if not pages:
        st.info("페이지별 읽기는 PDF 본문이 있어야 합니다. ① 원문 준비에서 PDF를 올려 주세요.")
        return
    T = _state(note, pages)
    ready = bool(st.session_state.get("openai_key"))
    st.caption("원문 쪽을 보며 질문에 답하고, 직접 2~3문장으로 요약합니다. 힌트는 단계별로 열리고, 모범 요약은 직접 눌렀을 때만 보입니다.")
    if not ready:
        need_key_notice()
    _mode(T, pages, pid)
    n = T["cur"]
    _print_bar(T, n, pid)
    _nav(T, pages, pid)
    ps = _page(T, n)
    label = tutor.page_label(T["print"], n)
    ctx = tutor.context(pages, n, label)
    doc_id = proj()["docs"].get(pid, {}).get("document_id") or f"{pid}:{len(pages)}"
    left, right = st.columns([1, 1], gap="medium")
    with left:
        _left(pid, n, pages, doc_id)
    with right:
        if ps.get("done"):
            st.markdown(":green-badge[이 쪽 끝냄]")
        _questions(pid, n, ps, ctx, label, ready)
        st.divider()
        _summary(pid, n, note, ps, ctx, label, pages, ready)
        _terms(pid, n, ps, ctx, ready)
