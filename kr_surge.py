"""국내 '영업(잠정)실적' 공시 중 전분기 대비 영업이익이 크게 늘어난 종목 찾기 (earnings_watch.py 가 쓴다).

자료: 금융감독원 전자공시(DART)
  목록  POST https://dart.fss.or.kr/dsab007/detailSearch.ax  (하루치 공시 중 제목에 '영업(잠정)실적')
  본문  https://dart.fss.or.kr/dsaf001/main.do?rcpNo=...  → 페이지 안 viewDoc(rcpNo, dcmNo, ...) 의 dcmNo
        → https://dart.fss.or.kr/report/viewer.do?rcpNo=..&dcmNo=..  (실적 표가 그대로 들어 있다)
종목코드와 공시 시각은 네이버(자동완성·종목 공시 목록)에서 채운다.

공시 표의 '영업이익 / 당해실적'  줄에서 당기·전기 실적과 전기대비 증감율, 흑자적자전환 여부를 읽어
  · 전기 대비 증감율이 MIN_CHG(%) 이상이거나 흑자전환이고
  · 영업이익이 MIN_OP(억원) 이상인
종목만 남긴다. 혼자 돌려 보려면: python kr_surge.py [YYYY-MM-DD]
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36"}
LIST_URL = "https://dart.fss.or.kr/dsab007/detailSearch.ax"
DOC_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={}"
VIEW_URL = "https://dart.fss.or.kr/report/viewer.do?rcpNo={}&dcmNo={}&eleId=0&offset=0&length=0&dtd=HTML"
TITLE = re.compile(r"영업\(잠정\)실적")
MIN_CHG = 50.0   # 전분기 대비 영업이익 증가율(%) 기준
MIN_OP = 10.0    # 영업이익 최소 규모(억원) - 작은 숫자가 몇 배로 뛴 것은 뺀다
UNIT = {"원": 1e-8, "천원": 1e-5, "백만원": 1e-2, "억원": 1.0, "십억원": 10.0}  # → 억원


def _get(url: str, data: bytes | None = None) -> str:
    """공시 본문(viewer)은 EUC-KR, 나머지는 UTF-8로 온다 → 문서가 밝힌 charset으로 읽는다."""
    req = urllib.request.Request(url, data=data, headers={**UA, "Referer": "https://dart.fss.or.kr/"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw, ct = r.read(), r.headers.get_content_charset()
    enc = ct or (re.search(rb'charset=["\']?([\w-]+)', raw[:2000]) or [b"", b"utf-8"])[1].decode()
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _text(html: str) -> str:
    """표를 '칸\t칸\n줄' 꼴의 글자로. 칸 안에 들어 있는 줄바꿈·공백은 없앤다."""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"</t[dh]>", "\x01", t, flags=re.I)          # 칸 끝
    t = re.sub(r"</tr>|<br\s*/?>", "\x02", t, flags=re.I)   # 줄 끝
    t = re.sub(r"<[^>]+>", " ", t).replace("&nbsp;", " ").replace("\xa0", " ")
    t = re.sub(r"\s*\x01\s*", "\x01", re.sub(r"[\r\n\t]+", " ", t))
    t = re.sub(r"\s*\x02\s*", "\x02", t)
    return re.sub(r"\n{2,}", "\n", t.replace("\x01", "\t").replace("\x02", "\n"))


def list_day(day: str) -> list[dict]:
    """하루치 공시 중 제목에 '영업(잠정)실적'이 든 것 (day: YYYYMMDD)."""
    out, page = [], 1
    while page <= 12:
        body = urllib.parse.urlencode({"currentPage": page, "maxResults": 100, "sort": "date", "series": "desc",
                                       "startDate": day, "endDate": day}).encode()
        html = _get(LIST_URL, body)
        rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
        if len(rows) <= 1:
            break
        for tr in rows:
            m = re.search(r"rcpNo=(\d+)[^>]*>\s*([^<]+?)\s*</a>", tr)
            if not m or not TITLE.search(m.group(2)):
                continue
            corp = re.search(r"기업개황 새창\"\s*>\s*([^<]+?)\s*</a>", tr)
            out.append({"rcp": m.group(1), "title": m.group(2).strip(), "corp": (corp.group(1).strip() if corp else "")})
        page += 1
    return out


def _num(s: str) -> float | None:
    s = s.replace(",", "").strip()
    if not s or s in "-–—":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def numbers(rcp: str) -> dict | None:
    """공시 본문에서 매출액·영업이익의 당기·전기 실적과 증감율을 읽는다."""
    m = re.search(r'viewDoc\("(\d+)",\s*"(\d+)"', _get(DOC_URL.format(rcp)))
    if not m:
        return None
    txt = _text(_get(VIEW_URL.format(m.group(1), m.group(2))))
    unit = next((UNIT[u] for u in ("십억원", "백만원", "천원", "억원", "원")
                 if re.search(r"단위\s*:\s*" + u, txt)), None)
    period = re.search(r"\((\d{2}년\s*\d\s*[QqＱ분기]+[^)]*)\)", txt)
    out: dict = {"unit": unit, "period": (period.group(1).replace(" ", "") if period else "")}
    for key, label in (("sales", "매출액"), ("op", "영업이익"), ("net", "당기순이익")):
        row = re.search(rf"^\s*{label}\s*\t\s*당해실적\s*\t(.*)$", txt, re.M)
        if not row:
            continue
        c = [x.strip() for x in row.group(1).split("\t")]
        # 당기실적 / 전기실적 / 전기대비증감율 / 흑자적자전환 / 전년동기실적 / 전년동기대비증감율 / 흑자적자전환
        out[key] = {"now": _num(c[0]) if c else None, "prev": _num(c[1]) if len(c) > 1 else None,
                    "chg": _num(c[2]) if len(c) > 2 else None, "turn": (c[3] if len(c) > 3 else "").strip(),
                    "yoy_prev": _num(c[4]) if len(c) > 4 else None, "yoy": _num(c[5]) if len(c) > 5 else None}
    return out if out.get("op") else None


def code_of(name: str) -> str | None:
    """종목명 → 종목코드 (네이버 자동완성). 이름이 꼭 같은 것만 쓴다."""
    try:
        j = json.loads(_get("https://ac.stock.naver.com/ac?target=stock&q=" + urllib.parse.quote(name)))
    except Exception:
        return None
    norm = lambda s: re.sub(r"[\s()㈜]|주식회사", "", s)
    for it in j.get("items") or []:
        if it.get("nationCode") == "KOR" and norm(it.get("name", "")) == norm(name):
            return it.get("code")
    return None


def disc_time(code: str, rcp: str, day: str) -> str | None:
    """네이버 종목 공시 목록에서 그 공시의 접수 시각 (YYYY-MM-DDTHH:MM 꼴)."""
    try:
        rows = json.loads(_get(f"https://m.stock.naver.com/api/stock/{code}/disclosure?pageSize=20&page=1"))
    except Exception:
        return None
    for r in rows if isinstance(rows, list) else []:
        dt = (r.get("datetime") or "")[:10].replace("-", "")
        if dt == day and TITLE.search(r.get("title") or ""):
            return r["datetime"]
    return None


def quarter_of(day: str) -> str:
    """잠정실적 공시 날짜 → 그 실적의 분기 끝 달 (YYYY.MM). 분기가 끝난 뒤 다음 석 달 안에 내므로 직전 분기로 본다."""
    y, m = int(day[:4]), int(day[4:6])
    q = (m - 1) // 3 * 3  # 직전 분기 끝 달 (1~3월이면 0 → 작년 12월)
    return f"{y - 1}.12" if q == 0 else f"{y}.{q:02d}"


def prelim(d: dict, day: str, n: dict) -> dict | None:
    """잠정실적 숫자를 억원으로 정리 (재무제표 공부 › 분기 실적에 쓴다)."""
    u = n.get("unit")
    if not u:
        return None
    cv = lambda v: None if v is None else round(v * u, 1)
    out = {"rcp": d["rcp"], "name": d["corp"], "day": f"{day[:4]}-{day[4:6]}-{day[6:]}", "q": quarter_of(day),
           "title": d["title"], "consol": "연결" in d["title"]}
    for k in ("sales", "op", "net"):
        x = n.get(k) or {}
        out[k] = [cv(x.get("now")), cv(x.get("prev")), cv(x.get("yoy_prev"))]  # 이번 분기 · 전분기 · 전년 동기
    return out


def check(d: dict, day: str, min_chg: float = MIN_CHG, min_op: float = MIN_OP, n: dict | None = None) -> dict | None:
    """공시 하나를 읽어 '전분기 대비 영업이익 급증'이면 정리해서 돌려준다 (아니면 None)."""
    n = n or numbers(d["rcp"])
    if not n or not n.get("unit"):
        return None
    op = n["op"]
    turn = "흑" in (op.get("turn") or "")
    if op.get("now") is None or (op["chg"] is None and not turn):
        return None
    amount = op["now"] * n["unit"]
    if amount < min_op or (not turn and (op["chg"] or 0) < min_chg):
        return None
    code = code_of(d["corp"])
    if not code:
        print("  종목코드 모름:", d["corp"], flush=True)
        return None
    return {"rcp": d["rcp"], "name": d["corp"], "code": code, "title": d["title"], "period": n["period"],
            "unit": n["unit"], "turn": turn, "op": op, "sales": n.get("sales"), "net": n.get("net"),
            "eok": amount, "chg": op["chg"], "at": disc_time(code, d["rcp"], day),
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={d['rcp']}"}


def surges(day: str, min_chg: float = MIN_CHG, min_op: float = MIN_OP) -> list[dict]:
    """그날 공시 중 전분기 대비 영업이익이 크게 늘어난 종목."""
    out = []
    for d in list_day(day):
        try:
            s = check(d, day, min_chg, min_op)
        except Exception as e:
            print("  본문 읽기 실패:", d["corp"], type(e).__name__, e, flush=True)
            continue
        if s:
            out.append(s)
    return out


def eok(v: float | None, unit: float) -> str:
    if v is None:
        return "-"
    a = v * unit
    return f"{a / 10000:,.0f}조" if abs(a) >= 10000 else f"{a:,.0f}억"


def summary(s: dict) -> list[str]:
    """카드·텔레그램에 넣을 요약 줄."""
    o, u = s["op"], s["unit"]
    head = f"영업이익 {eok(o['now'], u)}"
    head += " (전분기 적자 → 흑자전환)" if s["turn"] else f" · 전분기 {eok(o['prev'], u)} 대비 {o['chg']:+.1f}%"
    lines = [head]
    if s.get("sales") and s["sales"].get("now") is not None:
        sl = f"매출 {eok(s['sales']['now'], u)}"
        if s["sales"].get("chg") is not None:
            sl += f" (전분기 대비 {s['sales']['chg']:+.1f}%)"
        lines.append(sl)
    if o.get("yoy") is not None:
        lines.append(f"전년 동기 대비 {o['yoy']:+.1f}%")
    return lines


if __name__ == "__main__":
    d = sys.argv[1].replace("-", "") if len(sys.argv) > 1 else datetime.now(KST).strftime("%Y%m%d")
    rows = surges(d)
    print(f"{d}: 급증 {len(rows)}건")
    for s in rows:
        print(" *", s["name"], s["code"], s["period"], s["at"], "|", " / ".join(summary(s)))
