"""보고서·계획서·원고 내보내기: Word(.docx), 한글(.hwpx), 결과표(Excel·CSV).

모든 형식이 같은 blocks 목록에서 만들어지므로 내용과 참고문헌이 서로 달라지지 않는다 (REPORT-04).
block = (kind, text)  kind: title | meta | h | h2 | p | b(굵은 머리말+본문은 'label|text') | cap(표 제목) | kw(주제어) | ref(참고문헌)
                     | table(text 자리에 {"header":[...],"rows":[[...]]})
"""
from __future__ import annotations

FILE_VERSION = "3.2.0"

import io
import time
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from . import reporttpl
from .cite import format_citation
from .store import safe_cell

TPL = Path(__file__).parent / "hwpx_tpl"
THREE = ["기여", "증명방법", "검증 범위"]


def report_fields(proj: dict, pid: str) -> dict:
    """A4 보고서의 편집 가능한 칸. 사용자가 고친 값(edited)이 있으면 그것을, 없으면 AI 초안을 쓴다."""
    note = proj["notes"].get(pid, {})
    ed = note.get("edited", {})
    s6 = {k: (v or {}).get("text", "") for k, v in ((note.get("summary6") or {}).get("data") or {}).get("items", {}).items()}
    m = note.get("method") or {}
    sec = {k: (v or {}).get("text", "") for k, v in (m.get("sections") or {}).items()}
    three = m.get("three_lines") or {}
    ai = {
        "overview": " ".join(x for x in (s6.get("주제", ""), s6.get("목적", "")) if x),
        "tech": " ".join(x for x in (s6.get("다룬 기술", ""), s6.get("활용 분야", "")) if x),
        "contribution": three.get("기여", "") or sec.get("연구 기여도", ""),
        "proof": three.get("증명방법", "") or sec.get("연구방법·증명방법", ""),
        "scope": three.get("검증 범위", ""),
        "limits": sec.get("한계", "") or s6.get("주요 한계", ""),
    }
    out = {k: ed.get(k, v) for k, v in ai.items()}
    out["_ai"] = ai
    out["reason"] = note.get("selection_reason", "")
    out["extra_limits"] = note.get("extra_limits", "")
    out["thoughts"] = note.get("my_thoughts", "")
    return out


def _sec_blocks(sec: str, proj: dict, pid: str, f: dict) -> list[tuple[str, str]]:
    need = "[작성 필요]"
    note = proj["notes"].get(pid, {})
    if sec == "cite":
        return [("p", format_citation(proj["papers"][pid]))]
    if sec == "reason":
        return [("p", f["reason"] or need)]
    if sec == "overview":
        return [("p", f["overview"] or need)]
    if sec == "tech":
        return [("p", f["tech"] or need)]
    if sec == "three":
        return [("b", f"기여|{f['contribution'] or need}"), ("b", f"증명방법|{f['proof'] or need}"), ("b", f"검증 범위|{f['scope'] or need}")]
    if sec == "limits":
        return [("b", f"논문에 명시된 한계|{f['limits'] or need}"), ("b", f"내가 추가로 검토할 점|{f['extra_limits'] or need}")]
    if sec == "thoughts":
        return [("p", f["thoughts"] or need)]
    if sec == "s7":
        from .ai import SEVEN
        items = ((note.get("summary7") or {}).get("data") or {}).get("items") or {}
        if not items:
            return [("p", "[7문장 요약 작성 필요]")]
        return [("b", f"{k}|{(items.get(k) or {}).get('text') or need}") for k in SEVEN]
    if sec == "replication":
        rp = (note.get("method") or {}).get("replication") or {}
        return [("b", f"{k}|{v or need}") for k, v in rp.items()] or [("p", "[따라 해 보기 검토 작성 필요]")]
    return []


def report_tpl(proj: dict) -> dict:
    tpl = proj["project"].get("report_tpl")
    return tpl if reporttpl.valid(tpl) else reporttpl.default_tpl()


def report_blocks(proj: dict, pid: str, replicated: bool = False) -> list[tuple[str, str]]:
    """선택한 양식(항목·순서·제목)대로 보고서를 만든다. 번호는 켜진 항목 순서로 다시 매긴다."""
    f = report_fields(proj, pid)
    tpl = report_tpl(proj)
    c = proj["project"]["cover"]
    cover = " · ".join(x for x in (c.get("school"), c.get("major"), c.get("course"), c.get("name"), c.get("date")) if x)
    blocks = [("title", (tpl.get("title") or "").strip() or "논문 요약 보고서"), ("meta", cover or "[표지 정보 미입력]")]
    for i, sec in enumerate([s for s in tpl["sections"] if s.get("on")], start=1):
        blocks.append(("h", f"{i}. {(sec.get('label') or '').strip() or reporttpl.SEC_DEF[sec['id']]}"))
        blocks += _sec_blocks(sec["id"], proj, pid, f)
    if not replicated:
        blocks.append(("meta", "※ 이 보고서는 논문을 읽고 정리한 것이며, 논문의 방법을 직접 재현·실험한 결과는 포함하지 않습니다."))
    return blocks


def char_count(blocks) -> int:
    blocks = [(k, t) for k, t in blocks if isinstance(t, str)]
    return sum(len(t.replace("|", " ")) for _, t in blocks)


# ---------- Word ----------
def to_docx(blocks, font: str = "맑은 고딕") -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.left_margin = sec.right_margin = Mm(25)
    sec.top_margin = sec.bottom_margin = Mm(20)
    style = doc.styles["Normal"]
    style.font.name, style.font.size = font, Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), font)

    def para(text="", size=10.5, bold=False, align=None, after=4):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(after)
        p.paragraph_format.space_before = Pt(0)
        if align:
            p.alignment = align
        if text:
            r = p.add_run(text)
            r.font.size, r.bold = Pt(size), bold
            r.font.name = font
            r._element.rPr.rFonts.set(qn("w:eastAsia"), font)
        return p

    for kind, text in blocks:
        if kind == "title":
            para(text, 16, True, WD_ALIGN_PARAGRAPH.CENTER, 6)
        elif kind == "meta":
            para(text, 9, False, WD_ALIGN_PARAGRAPH.CENTER, 8)
        elif kind == "h":
            para(text, 11.5, True, after=3).paragraph_format.space_before = Pt(8)
        elif kind == "h2":
            para(text, 10.5, True, after=2).paragraph_format.space_before = Pt(4)
        elif kind == "cap":
            para(text, 9.5, True, WD_ALIGN_PARAGRAPH.CENTER, 2).paragraph_format.space_before = Pt(4)
        elif kind == "kw":
            para(text, 10, True, after=6)
        elif kind == "ref":
            para(text, 9.5, after=2)
        elif kind == "table":
            header, rows = text.get("header") or [], text.get("rows") or []
            t = doc.add_table(rows=1 + len(rows), cols=len(header))
            t.style = "Table Grid"
            for r, row in enumerate([header] + rows):
                for c, val in enumerate(row[:len(header)]):
                    cell = t.cell(r, c)
                    cell.text = ""
                    run = cell.paragraphs[0].add_run(str(val))
                    run.font.size, run.bold, run.font.name = Pt(9), r == 0, font
                    run._element.rPr.rFonts.set(qn("w:eastAsia"), font)
            para(after=2)
        elif kind == "b":
            label, _, body = text.partition("|")
            p = para(after=3)
            for t, b in ((label + ": ", True), (body, False)):
                r = p.add_run(t)
                r.bold, r.font.size, r.font.name = b, Pt(10.5), font
                r._element.rPr.rFonts.set(qn("w:eastAsia"), font)
        else:
            for line in text.split("\n"):
                para(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------- 한글 HWPX ----------
def _hp(pid: int, text: str, para_pr: int, char_pr: int) -> str:
    body = f"<hp:t>{escape(text)}</hp:t>" if text else "<hp:t/>"
    return (f'<hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
            f'<hp:run charPrIDRef="{char_pr}">{body}</hp:run></hp:p>')


def _hp_runs(pid: int, runs: list[tuple[str, int]], para_pr: int = 0) -> str:
    inner = "".join(f'<hp:run charPrIDRef="{c}"><hp:t>{escape(t)}</hp:t></hp:run>' for t, c in runs if t)
    return f'<hp:p id="{pid}" paraPrIDRef="{para_pr}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">{inner}</hp:p>'


def _hp_table(pid: int, t: dict) -> tuple[str, int]:
    """첫 행은 머리글(배경 borderFill 4, 굵게 9, 가운데 21), 나머지는 실선(3, 보통 0, 양쪽 22). 본문폭 42520."""
    header, rows = t.get("header") or [], t.get("rows") or []
    cols = max(1, len(header))
    widths = [42520 // cols] * cols
    widths[-1] += 42520 - sum(widths)
    row_h = 2400
    tbl_id, pid = pid, pid + 1
    trs = []
    for r, row in enumerate([header] + rows):
        tcs = []
        for c in range(cols):
            val = str(row[c]) if c < len(row) else ""
            head = r == 0
            body = f"<hp:t>{escape(val)}</hp:t>" if val else "<hp:t/>"
            tcs.append(
                f'<hp:tc name="" header="{1 if head else 0}" hasMargin="1" protect="0" editable="0" dirty="1" borderFillIDRef="{4 if head else 3}">'
                '<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" '
                'textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">'
                f'<hp:p paraPrIDRef="{21 if head else 22}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0" id="{pid}">'
                f'<hp:run charPrIDRef="{9 if head else 11}">{body}</hp:run></hp:p></hp:subList>'
                f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
                f'<hp:cellSz width="{widths[c]}" height="{row_h}"/><hp:cellMargin left="300" right="300" top="150" bottom="150"/></hp:tc>')
            pid += 1
        trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
    n = len(trs)
    xml = (f'<hp:p id="{pid}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">'
           f'<hp:tbl id="{tbl_id}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" '
           f'pageBreak="CELL" repeatHeader="1" rowCnt="{n}" colCnt="{cols}" cellSpacing="0" borderFillIDRef="3" noAdjust="0">'
           f'<hp:sz width="42520" widthRelTo="ABSOLUTE" height="{row_h * n}" heightRelTo="ABSOLUTE" protect="0"/>'
           '<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" '
           'vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>'
           '<hp:outMargin left="0" right="0" top="0" bottom="0"/><hp:inMargin left="0" right="0" top="0" bottom="0"/>'
           + "".join(trs) + "</hp:tbl></hp:run></hp:p>")
    return xml, pid + 1


def to_hwpx(blocks, title: str = "논문 요약 보고서") -> bytes:
    """보고서 양식 header(글자: 12=16pt 굵게, 13=12pt 굵게, 9=10pt 굵게, 11=9pt / 문단: 20=가운데, 27=구획 머리말)를 쓴다."""
    base = (TPL / "Contents" / "section0.xml").read_text(encoding="utf-8")
    head, tail = base.rsplit("</hs:sec>", 1)
    out, pid = [], 1000000001
    for kind, text in blocks:
        if kind == "title":
            out.append(_hp(pid, text, 20, 12))
        elif kind == "meta":
            out.append(_hp(pid, text, 20, 11))
        elif kind == "h":
            out.append(_hp(pid, text, 27, 13))
        elif kind == "h2":
            out.append(_hp(pid, text, 0, 9))
        elif kind == "cap":
            out.append(_hp(pid, text, 20, 9))
        elif kind == "kw":
            out.append(_hp(pid, text, 0, 9))
        elif kind == "ref":
            out.append(_hp(pid, text, 0, 11))
        elif kind == "table":
            xml, pid = _hp_table(pid, text)
            out.append(xml)
        elif kind == "b":
            label, _, body = text.partition("|")
            out.append(_hp_runs(pid, [(label + ": ", 9), (body, 0)]))
        else:
            for line in text.split("\n"):
                out.append(_hp(pid, line, 0, 0))
                pid += 1
        pid += 1
    section = head + "".join(out) + "</hs:sec>" + tail
    hpf = (TPL / "Contents" / "content.hpf").read_text(encoding="utf-8").replace("<opf:title/>", f"<opf:title>{escape(title)}</opf:title>")
    buf = io.BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as zf:
        zf.write(TPL / "mimetype", "mimetype", compress_type=ZIP_STORED)
        for path in sorted(TPL.rglob("*")):
            rel = path.relative_to(TPL).as_posix()
            if not path.is_file() or rel == "mimetype":
                continue
            if rel == "Contents/section0.xml":
                zf.writestr(rel, section)
            elif rel == "Contents/content.hpf":
                zf.writestr(rel, hpf)
            elif rel == "Preview/PrvText.txt":
                zf.writestr(rel, "\n".join(t.replace("|", ": ") for k, t in blocks if isinstance(t, str))[:1000])
            else:
                zf.write(path, rel)
    return buf.getvalue()


# ---------- 표 ----------
def to_xlsx(sheets: dict[str, list[dict]]) -> bytes:
    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, rows in sheets.items():
            df = pd.DataFrame(rows if rows else [{"안내": "자료 없음"}])
            df = df.map(lambda v: safe_cell(v if isinstance(v, (str, int, float, type(None))) else str(v)))
            df.to_excel(xw, sheet_name=name[:31], index=False)
    return buf.getvalue()


def to_csv(rows: list[dict]) -> bytes:
    import pandas as pd

    df = pd.DataFrame(rows).map(lambda v: safe_cell(v if isinstance(v, (str, int, float, type(None))) else str(v)))
    return df.to_csv(index=False).encode("utf-8-sig")


def stamp() -> str:
    return time.strftime("%Y%m%d_%H%M")
