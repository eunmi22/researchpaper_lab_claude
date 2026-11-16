"""합성 응답. 활용가이드의 응답 구조를 따른 시험용 자료이며 실제 검색 결과가 아니다."""


def item(name, value):
    return f"<item><value>{value}</value><name>{name}</name></item>"


def record(**f):
    return "<record>" + "".join(item(k, v) for k, v in f.items()) + "</record>"


def response(total, records, code="00"):
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response><header><resultMsg>NORMAL_CODE</resultMsg>'
            f"<resultCode>{code}</resultCode></header><total>{total}</total>{''.join(records)}</response>").encode("utf-8")


THESIS = record(controlno="KDMT1202600000001", **{"자료명/저자사항": "합성 학위논문 제목 &lt;b&gt;포렌식&lt;/b&gt; =Synthetic thesis /홍길동",
                                                  "발행자": "가상대학교 대학원,2026", "발행년": "2026", "DB": "학위논문"})
ARTICLE = record(controlno="KINX2026000002", **{"자료명/저자사항": "합성 학술기사 제목 /김철수,이영희", "발행자": "가상학회,2025. 6. 30",
                                               "발행년": "2025", "수록지명": "가상학회지.제 3 권 제 2 호 (2025 년 6 월),", "DB": "국내학술기사"})
BOOK = record(controlno="MONO12026000003", **{"자료명/저자사항": "합성 도서 /박저자", "발행자": "가상출판사,2024", "발행년": "2024", "DB": "도서자료"})
CONFLICT = record(controlno="MONO12026000004", **{"자료명/저자사항": "유형 충돌 자료 /무명", "발행년": "2023", "DB": "학위논문"})
NOTYPE = record(controlno="ZZZZ2026000005", **{"자료명/저자사항": "논문이라는 단어가 든 제목", "발행년": "2022"})
NOID = record(**{"자료명/저자사항": "관리번호 없는 자료 /무명", "DB": "국내학술기사"})


class FakeResp:
    def __init__(self, content, status=200):
        self.content, self.status_code, self.text = content, status, content.decode("utf-8", "replace")


class FakeSession:
    """페이지 번호별 응답을 돌려준다. 값이 Exception이면 던진다."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, params=None, **kw):
        self.calls.append(dict(params or {}))
        out = self.pages[params["pageno"]]
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, FakeResp) else FakeResp(out)
