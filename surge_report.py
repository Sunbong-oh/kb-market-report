"""실적카드(전분기 대비 영업이익 급증) 종목 요약 보고서 (surge-report.yml 이 카드 직후 실행).

earnings_watch.py 가 국내 잠정실적 급증 카드를 텔레그램으로 보내면, live_snapshot.py 가 이 보고서 작업을 띄운다.
종목마다 KB 리서치 스타일 HTML 한 장을 만들어 텔레그램에 파일로 보내고, surge-reports 브랜치에 보관한다.

보고서 순서 (사용자 요청 순)
  1. 일봉 차트 (1년, 20/60/120일 이동평균, 거래량)      KB증권 Open API IVS11560 → 실패 시 네이버(2차 출처)
  2. 최근 3개월 수급 (외국인·기관·개인 순매수 누적)     KB증권 Open API IVU10430 → 실패 시 네이버(2차 출처)
  3. 10년 영업이익 막대 + 영업이익률 선, 올해 추정치     OpenDART fnlttSinglAcnt (연결 우선) + 산출값
  4. 최근 3년 분기 영업이익                               OpenDART (4분기 = 연간 − 3분기 누적, 산출값) + 이번 잠정실적
  5. 미상환 CB·BW·EB                                      정기보고서 '미상환 … 발행현황' 표 + 발행결정·전환청구 공시
  6. 주요 사업·매출 구성·주요 매출처                     최근 사업보고서 원문 (II. 사업의 내용)
프로젝트 필터(4개 분기 연속 영업적자, PBR 25배 이상)에 걸리면 표지에 '제외 대상'으로 표시한다.
추정치는 컨센서스가 아니라 과거 실적으로 계산한 산출값이고, 보고서에 계산 방식을 적는다.

  python surge_report.py 300120                         # out/surge_reports/ 에 HTML
  python surge_report.py 300120:20261021900206 --send   # 잠정실적 공시 지정 + 텔레그램 파일 발송
환경변수: DART_API_KEY (필수), KB_OPENAPI_APP_KEY / KB_OPENAPI_APP_SECRET, TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out" / "surge_reports"
DART = "https://opendart.fss.or.kr/api/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
      "Referer": "https://m.stock.naver.com/"}
REPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}  # 1분기, 반기, 3분기, 사업보고서
TODAY = datetime.now(KST).date()


def log(*a) -> None:
    print(*a, flush=True)


# ------------------------------------------------------------------ OpenDART
def dart(path: str, raw: bool = False, timeout: int = 60, **params):
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise RuntimeError("환경변수 DART_API_KEY 가 없습니다 (GitHub Secrets 에 등록).")
    url = DART + path + "?" + urllib.parse.urlencode({"crtfc_key": key, **params})
    for i in range(3):
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA["User-Agent"]}), timeout=timeout).read()
            if raw:
                return b
            j = json.loads(b.decode("utf-8"))
            if j.get("status") not in ("000", "013"):
                log("  DART", path, j.get("status"), j.get("message"))
            return j
        except Exception as e:
            log("  DART 재시도", path, type(e).__name__, e)
            time.sleep(2 * (i + 1))
    return None if raw else {}


def corp_map() -> dict[str, tuple[str, str]]:
    """종목코드 → (corp_code, 회사명). 하루 한 번만 받아 out/ 에 둔다 (zip 3.6MB)."""
    cache = ROOT / "out" / f"corpcode_{TODAY:%Y%m%d}.json"
    if cache.exists():
        return {k: tuple(v) for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
    z = zipfile.ZipFile(io.BytesIO(dart("corpCode.xml", raw=True, timeout=500)))
    root = ET.fromstring(z.read(z.namelist()[0]))
    m = {}
    for e in root.iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:
            m[sc] = (e.findtext("corp_code"), (e.findtext("corp_name") or "").strip())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return m


def amt(v) -> float | None:
    """'1,234,000' (원) → 억원."""
    s = str(v or "").replace(",", "").strip()
    if not s or s == "-":
        return None
    try:
        return float(s) / 1e8
    except ValueError:
        return None


SALES = ("매출액", "수익(매출액)", "영업수익", "매출", "수익")


def _pick(rows: list[dict]) -> tuple[str, dict, dict] | None:
    """한 보고서의 주요계정에서 (fs_div, 매출 행, 영업이익 행). 연결(CFS) 우선."""
    for fs in ("CFS", "OFS"):
        rs = [r for r in rows if r.get("fs_div") == fs]
        op = next((r for r in rs if r.get("account_nm") == "영업이익"), None)
        if not op:
            continue
        sales = next((r for n in SALES for r in rs if r.get("account_nm") == n), {})
        return fs, sales, op
    return None


def statement(cc: str, year: int, q: int) -> dict | None:
    j = dart("fnlttSinglAcnt.json", corp_code=cc, bsns_year=year, reprt_code=REPRT[q])
    rows = (j or {}).get("list") or []
    p = _pick(rows)
    if not p:
        return None
    fs, s, o = p
    eq = next((r for r in rows if r.get("fs_div") == fs and r.get("account_nm") == "자본총계"), {})
    return {"fs": fs, "rcp": o.get("rcept_no"), "s": s, "o": o, "eq": amt(eq.get("thstrm_amount"))}


def annual_series(cc: str, latest: int) -> dict[int, dict]:
    """연간 매출·영업이익 (최근 사업보고서 연도부터 12년 전까지). 한 보고서가 당기·전기·전전기를 준다."""
    out: dict[int, dict] = {}
    for y in range(latest, latest - 12, -3):
        st = statement(cc, y, 4)
        if not st:
            continue
        for k, yy in (("thstrm_amount", y), ("frmtrm_amount", y - 1), ("bfefrmtrm_amount", y - 2)):
            if yy in out or amt(st["o"].get(k)) is None:
                continue
            out[yy] = {"sales": amt(st["s"].get(k)), "op": amt(st["o"].get(k)), "fs": st["fs"], "rcp": st["rcp"]}
    return dict(sorted(out.items()))


def quarter_series(cc: str, years: list[int]) -> list[dict]:
    """분기별 3개월 매출·영업이익. 4분기는 연간 − 3분기 누적(산출값)."""
    qs = []
    for y in years:
        st = {q: statement(cc, y, q) for q in (1, 2, 3, 4)}
        for q in (1, 2, 3):
            if st[q]:
                qs.append({"y": y, "q": q, "sales": amt(st[q]["s"].get("thstrm_amount")), "op": amt(st[q]["o"].get("thstrm_amount")),
                           "fs": st[q]["fs"], "rcp": st[q]["rcp"], "calc": False, "eq": st[q]["eq"]})
        if st[4]:
            a, c = st[4], st[3]
            q4 = lambda k: (amt(a[k].get("thstrm_amount")) - amt(c[k].get("thstrm_add_amount"))
                            if c and amt(a[k].get("thstrm_amount")) is not None and amt(c[k].get("thstrm_add_amount")) is not None else None)
            qs.append({"y": y, "q": 4, "op": q4("o"), "sales": q4("s"),
                       "fs": a["fs"], "rcp": a["rcp"], "calc": True, "eq": a["eq"]})
    return sorted(qs, key=lambda r: (r["y"], r["q"]))


def find_provisional(cc: str) -> str | None:
    """최근 30일 거래소 공시 중 '영업(잠정)실적' 접수번호."""
    j = dart("list.json", corp_code=cc, bgn_de=(TODAY - timedelta(days=30)).strftime("%Y%m%d"),
             end_de=TODAY.strftime("%Y%m%d"), pblntf_ty="I", page_count=100)
    for r in (j or {}).get("list") or []:
        if re.search(r"영업\(잠정\)실적", r.get("report_nm", "")):
            return r["rcept_no"]
    return None


def provisional(rcp: str) -> dict | None:
    """잠정실적 공시 본문 (kr_surge 와 같은 방식, dart.fss.or.kr) → 이번 분기."""
    try:
        import kr_surge
        n = kr_surge.numbers(rcp)
    except Exception as e:
        log("  잠정실적 본문 실패:", type(e).__name__, e)
        return None
    if not n or not n.get("unit"):
        return None
    m = re.search(r"(\d{2})년\s*(\d)", n.get("period") or "")
    u = n["unit"]
    g = lambda k, f: (n.get(k) or {}).get(f)
    return {"y": 2000 + int(m.group(1)) if m else None, "q": int(m.group(2)) if m else None, "period": n.get("period"),
            "op": g("op", "now") * u if g("op", "now") is not None else None,
            "sales": g("sales", "now") * u if g("sales", "now") is not None else None,
            "op_prev": g("op", "prev") * u if g("op", "prev") is not None else None,
            "chg": g("op", "chg"), "turn": "흑" in (g("op", "turn") or ""), "yoy": g("op", "yoy"), "rcp": rcp}


def estimate(qs: list[dict], year: int) -> dict | None:
    """올해 연간 추정(산출값). 남은 분기 = 전년 같은 분기 × (올해 누적 / 전년 동기 누적).
    누적이 적자이거나 비율이 0.2~5배 밖이면 최근 4개 분기 평균으로 남은 분기를 채운다."""
    cur = [r for r in qs if r["y"] == year and r["op"] is not None]
    if not cur or len(cur) >= 4:
        return None
    done = {r["q"] for r in cur}
    prev = {r["q"]: r for r in qs if r["y"] == year - 1}
    res, how = {}, ""
    for k in ("op", "sales"):
        ytd = sum(r[k] or 0 for r in cur)
        pytd = sum((prev.get(q) or {}).get(k) or 0 for q in done)
        rest = [prev.get(q, {}).get(k) for q in range(1, 5) if q not in done]
        ratio = ytd / pytd if pytd > 0 and ytd > 0 else None
        if ratio and 0.2 <= ratio <= 5 and None not in rest:
            res[k] = ytd + sum(rest) * ratio
            how = how or f"남은 {4 - len(done)}개 분기 = 전년 같은 분기 × 올해 누적 증가율({ratio:.2f}배)"
        else:
            last4 = [r[k] for r in qs if r[k] is not None][-4:]
            res[k] = ytd + (sum(last4) / len(last4)) * (4 - len(done)) if last4 else None
            how = how or f"남은 {4 - len(done)}개 분기 = 최근 4개 분기 평균"
    return {"op": res.get("op"), "sales": res.get("sales"), "how": how, "base": f"{year} {max(done)}Q 누적"}


def reports(cc: str) -> list[dict]:
    j = dart("list.json", corp_code=cc, bgn_de=(TODAY - timedelta(days=800)).strftime("%Y%m%d"),
             end_de=TODAY.strftime("%Y%m%d"), pblntf_ty="A", page_count=50)
    return [r for r in (j or {}).get("list") or [] if re.match(r"(사업|반기|분기)보고서", r.get("report_nm", ""))]


def document(rcp: str) -> str:
    b = dart("document.xml", raw=True, timeout=300, rcept_no=rcp)
    if not b:
        return ""
    try:
        z = zipfile.ZipFile(io.BytesIO(b))
    except zipfile.BadZipFile:
        return ""
    main = sorted(z.namelist(), key=len)[0]  # 본문 (첨부는 _00760 처럼 뒤에 번호가 붙는다)
    raw = z.read(main)
    for enc in ("utf-8", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


# ------------------------------------------------------- 정기보고서 원문 파싱
def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s.replace("&cr;", " ").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", s).strip()


def section(doc: str, title_re: str) -> str:
    """<TITLE> 기준 소제목 하나의 원문 (다음 TITLE 전까지)."""
    ts = list(re.finditer(r"<TITLE[^>]*>(.*?)</TITLE>", doc, re.S))
    for i, m in enumerate(ts):
        if re.search(title_re, _clean(m.group(1))):
            return doc[m.end(): ts[i + 1].start() if i + 1 < len(ts) else len(doc)]
    return ""


def blocks(frag: str) -> list:
    """원문 조각 → [("p", 글), ("t", [[(칸, colspan, rowspan), ...], ...]), ...] 순서대로."""
    out = []
    pos = 0
    for m in re.finditer(r"<TABLE.*?</TABLE>", frag, re.S):
        out += [("p", t) for t in _paras(frag[pos:m.start()])]
        rows = []
        for tr in re.findall(r"<TR[^>]*>(.*?)</TR>", m.group(0), re.S):
            cells = []
            for c in re.finditer(r"<(TD|TH|TE|TU)([^>]*)>(.*?)</\1>", tr, re.S):
                attr = c.group(2)
                cs = re.search(r'COLSPAN="?(\d+)', attr, re.I)
                rs = re.search(r'ROWSPAN="?(\d+)', attr, re.I)
                cells.append((_clean(c.group(3)), int(cs.group(1)) if cs else 1, int(rs.group(1)) if rs else 1))
            if cells:
                rows.append(cells)
        if rows:
            out.append(("t", rows))
        pos = m.end()
    out += [("p", t) for t in _paras(frag[pos:])]
    return out


def _paras(s: str) -> list[str]:
    parts = re.split(r"</P>|<BR\s*/?>|</SPAN>\s*(?=<SPAN)", s, flags=re.I)
    return [t for t in (_clean(p) for p in parts) if t]


def first_sentences(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind("다."), cut.rfind("니다."), cut.rfind(". "))
    return (cut[: end + 2] if end > limit * 0.5 else cut) + " …"


def business(doc: str) -> dict:
    """사업의 개요(발췌), 주요 제품 표, 매출 구성 비율, 주요 매출처 문단."""
    res: dict = {"overview": "", "products": None, "mix": [], "customers": [], "backlog": None}
    ov = [t for k, t in blocks(section(doc, r"^1\.\s*사업의\s*개요")) if k == "p"]
    res["overview"] = first_sentences(" ".join(ov), 1400)
    pb = blocks(section(doc, r"^2\.\s*주요\s*제품"))
    tbl = next((t for k, t in pb if k == "t" and len(t) >= 3), None)
    if tbl:
        res["products"] = tbl
        for row in tbl:
            label = row[0][0]
            pct = next((c[0] for c in row[1:] if re.fullmatch(r"-?\d+(?:[.,]\d+)?\s*%", c[0])), None)
            if pct and not re.search(r"합\s*계|^계$|총\s*계", label):
                v = float(pct.replace("%", "").replace(",", ".").strip())
                if 0 < v <= 100:
                    res["mix"].append((label, v))
    sb = blocks(section(doc, r"^4\.\s*매출\s*및\s*수주"))
    kw = re.compile(r"주요\s*(?:매출처|거래처|고객)")
    for i, (k, t) in enumerate(sb):
        txt = t if k == "p" else " ".join(c[0] for r in t for c in r)
        if kw.search(txt):
            if k == "p":
                start = kw.search(t).start()
                res["customers"].append(("p", first_sentences(t[max(0, start - 8):], 900)))
            else:
                res["customers"].append(("t", t))
            for k2, t2 in sb[i + 1: i + 4]:  # 바로 뒤 표·주석까지 (다음 소제목 전까지)
                if k2 == "p" and re.match(r"^(나|다|라)\.|^\(\d\)\s*(?!주요)", t2):
                    break
                res["customers"].append((k2, first_sentences(t2, 600) if k2 == "p" else t2))
            break
    for i, (k, t) in enumerate(sb):  # 수주잔고 표 (있으면)
        if k == "p" and re.search(r"수주\s*(?:에\s*관한|상황|현황)", t):
            res["backlog"] = next((t2 for k2, t2 in sb[i + 1: i + 4] if k2 == "t"), None)
            break
    return res


MEZZ_RE = r"미상환\s*(전환사채|신주인수권부사채|전환형\s*조건부\s*자본증권|교환사채)\s*발행\s*현황"


def mezz_tables(doc: str) -> list[dict]:
    """정기보고서 '미상환 전환사채/신주인수권부사채/… 발행현황' 표와 주석."""
    out = []
    bl = blocks(doc[doc.find("자금조달"):] if "자금조달" in doc else doc)
    for i, (k, t) in enumerate(bl):
        if k != "p":
            continue
        m = re.search(MEZZ_RE, t)
        if not m or len(t) > 80:
            continue
        item = {"kind": re.sub(r"\s+", "", m.group(1)), "table": None, "notes": [], "none": False}
        for k2, t2 in bl[i + 1: i + 6]:
            if k2 == "p" and re.search(MEZZ_RE + r"|채무증권\s*발행실적", t2):
                break
            if k2 == "t" and item["table"] is None:
                item["table"] = t2
            elif k2 == "p":
                if re.search(r"해당\s*(사항)?\s*(이|은)?\s*없", t2):
                    item["none"] = True
                if re.match(r"^\s*(주\s*\d|※|\*)", t2):
                    item["notes"].append(first_sentences(t2, 500))
        if item["table"] and _mezz_empty(item["table"]):
            item["none"] = True
        out.append(item)
    return out


def _mezz_empty(tbl: list) -> bool:
    """표에 회차·발행일 같은 실제 행이 없으면 (전부 '-') 미상환 없음."""
    body = [r for r in tbl if not re.search(r"종류|구분|회차|합\s*계|기준일|단위", r[0][0])]
    return all(all(c[0] in ("", "-", "—") for c in r) for r in body) if body else True


def decisions(cc: str) -> list[dict]:
    """최근 5년 CB·BW·EB 발행결정 (주요사항보고서). 만기가 아직 안 지난 것만."""
    bgn, end = (TODAY - timedelta(days=365 * 5)).strftime("%Y%m%d"), TODAY.strftime("%Y%m%d")
    out = []
    for ep, kind, prc, bgd, edd in (("cvbdIsDecsn", "CB", "cv_prc", "cvrqpd_bgd", "cvrqpd_edd"),
                                    ("bdwtIsDecsn", "BW", "ex_prc", "expd_bgd", "expd_edd"),
                                    ("exbdIsDecsn", "EB", "ex_prc", "exrqpd_bgd", "exrqpd_edd")):
        for r in (dart(ep + ".json", corp_code=cc, bgn_de=bgn, end_de=end) or {}).get("list") or []:
            mtd = _kdate(r.get("bd_mtd"))
            out.append({"kind": kind, "tm": r.get("bd_tm"), "knd": r.get("bd_knd"), "fta": amt(r.get("bd_fta")),
                        "mtd": mtd, "live": (mtd is None or mtd >= TODAY), "prc": r.get(prc) or r.get("cv_prc") or "-",
                        "period": f"{r.get(bgd) or '-'} ~ {r.get(edd) or '-'}", "vs": r.get("cvisstk_tisstk_vs") or r.get("nstk_isstk_tisstk_vs") or "-",
                        "floor": r.get("act_mktprcfl_cvprc_lwtrsprc") or "-", "rcp": r.get("rcept_no"), "bddd": r.get("bddd")})
    return sorted(out, key=lambda d: d["rcp"] or "")


def _kdate(s) -> date | None:
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(s or ""))
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


MEZZ_DISC = re.compile(r"전환청구권\s*행사|전환가액\s*의?\s*조정|행사가액\s*조정|신주인수권\s*행사|교환청구권\s*행사|"
                       r"교환가액\s*조정|만기\s*전\s*사채\s*취득|사채\s*취득|전환사채|신주인수권부사채|교환사채")


def mezz_disclosures(cc: str) -> list[dict]:
    j = dart("list.json", corp_code=cc, bgn_de=(TODAY - timedelta(days=365)).strftime("%Y%m%d"),
             end_de=TODAY.strftime("%Y%m%d"), page_count=100)
    return [{"dt": r["rcept_dt"], "t": r["report_nm"].strip(), "rcp": r["rcept_no"]}
            for r in (j or {}).get("list") or [] if MEZZ_DISC.search(r.get("report_nm", ""))]


def share_count(cc: str, year: int, q: int) -> dict | None:
    j = dart("stockTotqySttus.json", corp_code=cc, bsns_year=year, reprt_code=REPRT[q])
    for r in (j or {}).get("list") or []:
        if "보통" in (r.get("se") or ""):
            n = lambda k: float(str(r.get(k) or "0").replace(",", "").replace("-", "0") or 0)
            return {"issued": n("istc_totqy"), "treasury": n("tesstk_co"), "float": n("distb_stock_co")}
    return None


def parent_equity(cc: str, year: int, q: int) -> float | None:
    """지배기업 소유주지분 (전체 재무제표). 없으면 None."""
    for fs in ("CFS", "OFS"):
        j = dart("fnlttSinglAcntAll.json", corp_code=cc, bsns_year=year, reprt_code=REPRT[q], fs_div=fs)
        rows = [r for r in (j or {}).get("list") or [] if r.get("sj_div") == "BS"]
        if not rows:
            continue
        r = next((r for r in rows if r.get("account_id") == "ifrs-full_EquityAttributableToOwnersOfParent"
                  or re.search(r"지배기업\s*(의)?\s*소유주", r.get("account_nm", ""))), None)
        if r is None and fs == "OFS":
            r = next((r for r in rows if r.get("account_nm") == "자본총계"), None)
        if r is not None:
            return amt(r.get("thstrm_amount"))
    return None


# ------------------------------------------------------------ 시세·수급
def kb_data(code: str, market: str) -> dict:
    """KB증권 Open API (투자정보 TR만). 키가 없거나 실패하면 빈 값 → 네이버로 대신한다."""
    try:
        from app.config import load_settings
        from app.kb_client import KBClient
        from app.market import find_records, num, text
    except Exception as e:
        log("  KB 모듈 없음:", e)
        return {}
    s = load_settings()
    if s.demo_mode:
        log("  KB 키 없음 → 건너뜀")
        return {}

    async def run() -> dict:
        kb = KBClient(s)
        res: dict = {}
        try:
            for info in ("2", "1"):  # 2 = 수정주가 (명세 기준), 안 되면 1
                try:
                    d = await kb.call("IVS11560", {"info_ccd": info, "mkt_clsf": "1" if market == "K" else "0", "chrt_clsf": "D",
                                                   "minute_tck_indx": "", "is_cd": code, "inq_clsf": "2", "strt_dy": "", "inq_cnt": "260"})
                except Exception as e:
                    log("  KB 일봉 실패:", info, type(e).__name__, str(e)[:160])
                    continue
                recs = find_records(d, "cls_prc_p2")
                bars = sorted(({"d": text(r, "dt"), "o": num(r, "opn_prc_p2"), "h": num(r, "hgh_prc_p2"), "l": num(r, "lw_prc_p2"),
                                "c": num(r, "cls_prc_p2"), "v": num(r, "vlm")} for r in recs
                               if re.fullmatch(r"20\d{6}", text(r, "dt")) and text(r, "dt") <= TODAY.strftime("%Y%m%d")),
                              key=lambda b: b["d"])
                log(f"  KB 일봉 info_ccd={info}: {len(bars)}개", bars[-1] if bars else "", (recs[:1] or [""])[0] if not bars else "")
                if len(bars) >= 20:
                    res["bars"] = bars
                    break
            try:
                q = await kb.call("IVU10140", {"excg_clsf": "1", "shrt_cd": code})
                r = (find_records(q, "now_prc") or [{}])[0]
                res["quote"] = {"price": num(r, "now_prc"), "prev": num(r, "bdy_cls_prc"), "name": text(r, "is_nm")}
                log("  KB 현재가:", res["quote"])
            except Exception as e:
                log("  KB 현재가 실패:", type(e).__name__, str(e)[:160])
            start = (TODAY - timedelta(days=100)).strftime("%Y%m%d")
            try:
                d = await kb.call("IVU10430", {"excg_clsf": "1", "is_cd": code, "strt_dt": start, "end_dt": TODAY.strftime("%Y%m%d"),
                                               "amt_q_clsf": "1", "trd_clsf": "1", "acml_clsf": "0"})
                recs = find_records(d, "fgnr")
                log("  KB 수급 원문 첫 행:", (recs[:1] or [d])[0] if recs else str(d)[:300])
                flows = sorted(({"d": text(r, "dt"), "f": num(r, "fgnr"), "i": num(r, "ogn"), "p": num(r, "indv")} for r in recs
                                if re.fullmatch(r"20\d{6}", text(r, "dt"))), key=lambda x: x["d"])
                if len(flows) >= 10:
                    res["flows"], res["flow_unit"] = flows, "KB 원문 단위(금액)"
            except Exception as e:
                log("  KB 수급 실패:", type(e).__name__, str(e)[:160])
        finally:
            await kb.close()
        return res

    try:
        return asyncio.run(run())
    except Exception as e:
        log("  KB 실패:", type(e).__name__, e)
        return {}


def _naver(url: str):
    try:
        return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read().decode("utf-8"))
    except Exception as e:
        log("  네이버 실패:", url[:80], type(e).__name__, e)
        return None


def _f(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def naver_bars(code: str) -> list[dict]:
    s, e = (TODAY - timedelta(days=380)).strftime("%Y%m%d"), TODAY.strftime("%Y%m%d")
    j = _naver(f"https://api.stock.naver.com/chart/domestic/item/{code}/day?startDateTime={s}0000&endDateTime={e}2359")
    rows = j if isinstance(j, list) else (j or {}).get("priceInfos") or []
    bars = [{"d": str(r.get("localDate") or r.get("localTradedAt") or "")[:10].replace("-", ""), "o": _f(r.get("openPrice")),
             "h": _f(r.get("highPrice")), "l": _f(r.get("lowPrice")), "c": _f(r.get("closePrice")),
             "v": _f(r.get("accumulatedTradingVolume"))} for r in rows]
    return sorted((b for b in bars if re.fullmatch(r"20\d{6}", b["d"]) and b["c"]), key=lambda b: b["d"])


def naver_flows(code: str) -> list[dict]:
    """네이버 종목 투자자별 매매동향 (순매수 수량, 주). 한 페이지 최대 몇십 일이라 3개월치를 넘겨받는다."""
    out: dict[str, dict] = {}
    bizdate = ""
    for _ in range(4):
        j = _naver(f"https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60&bizdate={bizdate}")
        rows = j if isinstance(j, list) else []
        if not rows:
            break
        for r in rows:
            d = str(r.get("bizdate") or "").replace("-", "")
            if re.fullmatch(r"20\d{6}", d):
                out[d] = {"d": d, "f": _f(r.get("foreignerPureBuyQuant")), "i": _f(r.get("organPureBuyQuant")),
                          "p": _f(r.get("individualPureBuyQuant"))}
        oldest = min(out) if out else ""
        if not oldest or oldest <= (TODAY - timedelta(days=95)).strftime("%Y%m%d"):
            break
        bizdate = (datetime.strptime(oldest, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
    return sorted(out.values(), key=lambda x: x["d"])


def market_data(code: str, market: str) -> dict:
    cached = Path(os.environ.get("SURGE_MARKET_DIR", "-")) / f"{code}.json"
    if cached.is_file():  # 시험용: GitHub 작업에서 받아 둔 시세·수급
        return json.loads(cached.read_text(encoding="utf-8"))
    stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    kb = kb_data(code, market)
    res = {"bars": kb.get("bars"), "bars_src": f"KB증권 Open API IVS11560 ({stamp} 조회)" if kb.get("bars") else None,
           "flows": kb.get("flows"), "flows_src": f"KB증권 Open API IVU10430 ({stamp} 조회)" if kb.get("flows") else None,
           "flow_unit": kb.get("flow_unit"), "quote": kb.get("quote")}
    if not res["bars"]:
        b = naver_bars(code)
        if b:
            res["bars"], res["bars_src"] = b, f"2차 출처: 네이버 증권 일봉 ({stamp} 조회, KB API 조회 실패)"
    if not res["flows"]:
        f = naver_flows(code)
        if f:
            res["flows"], res["flow_unit"] = f, "주(순매수 수량)"
            res["flows_src"] = f"2차 출처: 네이버 증권 투자자별 매매동향 ({stamp} 조회, KB API 조회 실패)"
    return res


# ------------------------------------------------------------------ 차트(SVG)
def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def fnum(v, d: int = 0) -> str:
    if v is None:
        return "-"
    s = f"{v:,.{d}f}"
    return f'<span class="neg">{s}</span>' if v < 0 else s


def nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / n
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = (lo // step) * step
    out, v = [], start
    while v <= hi + step * 0.001:
        out.append(round(v, 10))
        v += step
    return out


def svg_candles(bars: list[dict]) -> str:
    bars = bars[-250:]
    W, H, L, R, T, VH = 860, 380, 8, 62, 14, 70
    PH = H - T - VH - 34
    lo = min(b["l"] or b["c"] for b in bars)
    hi = max(b["h"] or b["c"] for b in bars)
    pad = (hi - lo) * 0.06 or hi * 0.05
    lo, hi = lo - pad, hi + pad
    n = len(bars)
    bw = (W - L - R) / n
    x = lambda i: L + bw * (i + 0.5)
    y = lambda v: T + PH * (1 - (v - lo) / (hi - lo))
    vmax = max(b["v"] or 0 for b in bars) or 1
    vy0 = T + PH + 20 + VH
    g = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="일봉 차트">']
    for t in nice_ticks(lo, hi, 5):
        if lo <= t <= hi:
            g.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="ax" x="{W - R + 6}" y="{y(t) + 4:.1f}">{t:,.0f}</text>')
    last_m = ""
    for i, b in enumerate(bars):
        m = b["d"][:6]
        if m != last_m and i > 0 and b["d"][4:6] in ("01", "04", "07", "10"):
            g.append(f'<line class="grid" x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{T}" y2="{vy0}" stroke-dasharray="2 3"/>'
                     f'<text class="ax" x="{x(i) + 3:.1f}" y="{H - 6}">{b["d"][2:4]}.{b["d"][4:6]}</text>')
        last_m = m
    for i, b in enumerate(bars):
        o, c = b["o"] or b["c"], b["c"]
        h, l = b["h"] or max(o, c), b["l"] or min(o, c)
        cls = "up" if c >= o else "dn"
        g.append(f'<line class="wick {cls}" x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{y(h):.1f}" y2="{y(l):.1f}"/>')
        top, bot = y(max(o, c)), y(min(o, c))
        g.append(f'<rect class="cdl {cls}" x="{x(i) - bw * 0.36:.1f}" y="{top:.1f}" width="{max(bw * 0.72, 0.8):.1f}" height="{max(bot - top, 0.8):.1f}"/>')
        vh = (b["v"] or 0) / vmax * VH
        g.append(f'<rect class="vol {cls}" x="{x(i) - bw * 0.36:.1f}" y="{vy0 - vh:.1f}" width="{max(bw * 0.72, 0.8):.1f}" height="{vh:.1f}"/>')
    closes = [b["c"] for b in bars]
    for n_, cls in ((20, "ma20"), (60, "ma60"), (120, "ma120")):
        pts = [f"{x(i):.1f},{y(sum(closes[i - n_ + 1:i + 1]) / n_):.1f}" for i in range(n_ - 1, len(closes))]
        if len(pts) > 1:
            g.append(f'<polyline class="ma {cls}" points="{" ".join(pts)}"/>')
    lc = closes[-1]
    g.append(f'<line x1="{L}" x2="{W - R}" y1="{y(lc):.1f}" y2="{y(lc):.1f}" class="lastl"/>'
             f'<rect x="{W - R + 2}" y="{y(lc) - 9:.1f}" width="{R - 4}" height="18" class="lastb"/>'
             f'<text class="lastt" x="{W - R + 6}" y="{y(lc) + 4:.1f}">{lc:,.0f}</text>')
    g.append(f'<text class="ax" x="{L}" y="{vy0 - VH - 4}">거래량</text></svg>')
    return "".join(g)


def svg_flows(flows: list[dict]) -> str:
    W, H, L, R, T, B = 860, 300, 8, 74, 14, 28
    keys = (("f", "외국인", "fl-f"), ("i", "기관", "fl-i"), ("p", "개인", "fl-p"))
    cum = {k: [] for k, _, _ in keys}
    for k, _, _ in keys:
        s = 0.0
        for r in flows:
            s += r[k] or 0
            cum[k].append(s)
    allv = [v for k in cum for v in cum[k]] + [0]
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    n = len(flows)
    x = lambda i: L + (W - L - R) * (i / max(n - 1, 1))
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    g = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="투자자별 순매수 누적">']
    for t in nice_ticks(lo, hi, 5):
        if lo <= t <= hi:
            g.append(f'<line class="grid{" zero" if t == 0 else ""}" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="ax" x="{W - R + 6}" y="{y(t) + 4:.1f}">{_short(t)}</text>')
    step = max(1, n // 6)
    for i in range(0, n, step):
        d = flows[i]["d"]
        g.append(f'<text class="ax" x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">{d[4:6]}.{d[6:8]}</text>')
    for k, label, cls in keys:
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(cum[k]))
        g.append(f'<polyline class="fl {cls}" points="{pts}"/>')
        g.append(f'<text class="vl" x="{x(n - 1) - 4:.1f}" y="{y(cum[k][-1]) - 6:.1f}" text-anchor="end">{label}</text>')
    g.append("</svg>")
    return "".join(g)


def _short(v: float) -> str:
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:,.1f}억"
    if a >= 1e4:
        return f"{v / 1e4:,.0f}만"
    return f"{v:,.0f}"


def svg_combo(items: list[dict], aria: str) -> str:
    """막대 = 영업이익(억원), 선 = 영업이익률(%). items: {lab, op, opm, kind: ''|'est'|'calc'|'prov'}."""
    W, H, L, R, T, B = 860, 330, 56, 52, 26, 40
    ops = [i["op"] for i in items if i["op"] is not None] + [0]
    lo, hi = min(ops), max(ops)
    pad = (hi - lo) * 0.12 or 1
    lo, hi = (lo - pad if lo < 0 else 0), hi + pad
    ms = [i["opm"] for i in items if i["opm"] is not None] + [0]
    mlo, mhi = min(ms), max(ms)
    mpad = (mhi - mlo) * 0.15 or 1
    mlo, mhi = (mlo - mpad if mlo < 0 else 0), mhi + mpad
    n = len(items)
    bw = (W - L - R) / n
    x = lambda i: L + bw * (i + 0.5)
    y = lambda v: T + (H - T - B) * (1 - (v - lo) / (hi - lo))
    ym = lambda v: T + (H - T - B) * (1 - (v - mlo) / (mhi - mlo))
    g = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{esc(aria)}">',
         '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
         '<rect width="6" height="6" class="hbg"/><line x1="0" y1="0" x2="0" y2="6" class="hln"/></pattern></defs>']
    for t in nice_ticks(lo, hi, 5):
        if lo <= t <= hi:
            g.append(f'<line class="grid{" zero" if t == 0 else ""}" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                     f'<text class="ax" x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{t:,.0f}</text>')
    for t in nice_ticks(mlo, mhi, 5):
        if mlo <= t <= mhi:
            g.append(f'<text class="ax op" x="{W - R + 6}" y="{ym(t) + 4:.1f}">{t:.0f}%</text>')
    g.append(f'<text class="ax" x="{L - 6}" y="{T - 10}" text-anchor="end">억원</text>')
    for i, it in enumerate(items):
        if it["op"] is None:
            g.append(f'<text class="ax" x="{x(i):.1f}" y="{y(0) - 4:.1f}" text-anchor="middle">n/a</text>')
        else:
            y0, y1 = y(0), y(it["op"])
            cls = {"est": "bar est", "prov": "bar prov", "calc": "bar"}.get(it.get("kind"), "bar")
            if it["op"] < 0:
                cls += " lossb"
            g.append(f'<rect class="{cls}" x="{x(i) - bw * 0.32:.1f}" y="{min(y0, y1):.1f}" width="{bw * 0.64:.1f}" height="{max(abs(y1 - y0), 0.8):.1f}"/>')
            ty = y1 - 5 if it["op"] >= 0 else y1 + 13
            g.append(f'<text class="vl" x="{x(i):.1f}" y="{ty:.1f}" text-anchor="middle">{it["op"]:,.0f}</text>')
        mark = {"est": "E", "calc": "*", "prov": "P"}.get(it.get("kind"), "")
        g.append(f'<text class="ax" x="{x(i):.1f}" y="{H - B + 16}" text-anchor="middle">{esc(it["lab"])}{mark}</text>')
    pts = [(x(i), ym(it["opm"])) for i, it in enumerate(items) if it["opm"] is not None]
    if len(pts) > 1:
        g.append(f'<polyline class="opl" points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}"/>')
    for a, b in pts:
        g.append(f'<circle class="opd" cx="{a:.1f}" cy="{b:.1f}" r="3.2"/>')
    g.append("</svg>")
    return "".join(g)


def mix_bar(mix: list[tuple[str, float]]) -> str:
    if not mix:
        return ""
    cols = ("var(--kb)", "var(--ink)", "#B9A37A", "#8C8577", "#D9C690", "#5E594F", "#E6DCC4")
    tot = sum(v for _, v in mix) or 1
    seg = "".join(f'<span style="flex:{v / tot:.4f};background:{cols[i % len(cols)]}" title="{esc(l)} {v:.1f}%"></span>' for i, (l, v) in enumerate(mix))
    leg = "".join(f'<span><i style="background:{cols[i % len(cols)]}"></i>{esc(l[:30])} {v:.1f}%</span>' for i, (l, v) in enumerate(mix))
    return f'<div class="mix">{seg}</div><div class="legend">{leg}</div>'


def table_html(tbl: list, cut: int = 90) -> str:
    rows = []
    for r in tbl:
        cells = []
        for txt, cs, rs in r:
            t = txt if len(txt) <= cut else txt[:cut] + "…"
            attrs = (f' colspan="{cs}"' if cs > 1 else "") + (f' rowspan="{rs}"' if rs > 1 else "")
            cells.append(f"<td{attrs}>{esc(t)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<div class="tw"><table class="raw">' + "".join(rows) + "</table></div>"


# ------------------------------------------------------------------ 조립
def build(code: str, rcp: str | None, cmap: dict) -> dict:
    cc, cname = cmap[code]
    co = dart("company.json", corp_code=cc) or {}
    market = co.get("corp_cls") or "Y"
    name = co.get("stock_name") or cname
    log(f"■ {name}({code}) corp_code={cc} 시장={market}")
    reps = reports(cc)
    annual_rep = next((r for r in reps if r["report_nm"].startswith("사업보고서")), None)
    latest_rep = reps[0] if reps else None
    latest_year = int(re.search(r"\((\d{4})\.", annual_rep["report_nm"]).group(1)) if annual_rep else TODAY.year - 1
    ann = annual_series(cc, latest_year)
    qs = quarter_series(cc, list(range(TODAY.year - 3, TODAY.year + 1)))
    rcp = rcp or find_provisional(cc)
    prov = provisional(rcp) if rcp else None
    if prov and prov["y"] and prov["op"] is not None and not any(r["y"] == prov["y"] and r["q"] == prov["q"] for r in qs):
        qs.append({"y": prov["y"], "q": prov["q"], "op": prov["op"], "sales": prov["sales"], "fs": "잠정", "rcp": rcp,
                   "calc": False, "prov": True, "eq": None})
    qs = sorted(qs, key=lambda r: (r["y"], r["q"]))[-12:]
    cur_year = qs[-1]["y"] if qs else TODAY.year
    est = estimate(qs, cur_year) if cur_year not in ann else None
    # 주식수·자본 (가장 최근 정기보고서 기준)
    lq = None
    if latest_rep:
        m = re.search(r"\((\d{4})\.(\d{2})\)", latest_rep["report_nm"])
        if m:
            lq = (int(m.group(1)), {"03": 1, "06": 2, "09": 3, "12": 4}.get(m.group(2), 4))
    shares = share_count(cc, *lq) if lq else None
    equity = parent_equity(cc, *lq) if lq else None
    mkt = market_data(code, market)
    price = (mkt.get("quote") or {}).get("price") or (mkt["bars"][-1]["c"] if mkt.get("bars") else None)
    doc_a = document(annual_rep["rcept_no"]) if annual_rep else ""
    doc_l = doc_a if latest_rep and annual_rep and latest_rep["rcept_no"] == annual_rep["rcept_no"] else (document(latest_rep["rcept_no"]) if latest_rep else "")
    return {"code": code, "name": name, "cc": cc, "market": market, "co": co, "ann": ann, "qs": qs, "prov": prov, "est": est,
            "cur_year": cur_year, "annual_rep": annual_rep, "latest_rep": latest_rep, "lq": lq, "shares": shares, "equity": equity,
            "mkt": mkt, "price": price, "biz": business(doc_a) if doc_a else None, "mezz": mezz_tables(doc_l) if doc_l else [],
            "decs": decisions(cc), "mdisc": mezz_disclosures(cc)}


CSS = """
:root{--kb:#F2A900;--ink:#3D3A35;--paper:#FAF8F3;--sheet:#FFFFFF;--line:#E4DED2;--muted:#7A746A;--soft:#F6EFDC;--neg:#B4442F;--pos:#2F6B4F;
--barc:#C9C2B4;--up:#D2463C;--dn:#2F66B0;--f:#2F66B0;--i:#F2A900;--p:#8C8577;--sans:"Noto Sans KR","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#ECE7DD;--paper:#1D1B18;--sheet:#262420;--line:#3E3A33;--muted:#A9A296;
--soft:#332C1B;--neg:#E68A73;--pos:#7FC3A0;--barc:#5B564D;--up:#F07A6E;--dn:#79A6E8;--f:#79A6E8;--p:#B8B0A2;color-scheme:dark}}
:root[data-theme="dark"]{--ink:#ECE7DD;--paper:#1D1B18;--sheet:#262420;--line:#3E3A33;--muted:#A9A296;--soft:#332C1B;--neg:#E68A73;--pos:#7FC3A0;
--barc:#5B564D;--up:#F07A6E;--dn:#79A6E8;--f:#79A6E8;--p:#B8B0A2;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:14px;line-height:1.65;padding:24px 16px}
.page{max-width:900px;margin:0 auto 28px;background:var(--sheet);border:1px solid var(--line)}
.band{background:var(--kb);color:#3D3A35;display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:8px 28px;font-size:12px;font-weight:700;letter-spacing:.04em}
.inner{padding:22px 28px 8px}
.foot{display:flex;justify-content:space-between;gap:12px;border-top:2px solid var(--kb);margin:16px 28px 0;padding:8px 0 14px;font-size:11px;color:var(--muted)}
.foot b{color:var(--ink)}
h1{font-size:24px;font-weight:900;line-height:1.3;margin:6px 0 4px}
h2{font-size:17px;font-weight:900;margin:4px 0 10px;padding-left:10px;border-left:5px solid var(--kb)}
h3{font-size:14px;font-weight:700;margin:16px 0 6px}
p{margin:0 0 10px}
.kicker{font-size:12px;font-weight:700;color:var(--muted);letter-spacing:.06em}
.cover{display:grid;grid-template-columns:minmax(0,1fr) 260px;gap:24px}
.keydata{background:var(--soft);border-top:4px solid var(--ink);padding:14px 16px;align-self:start}
.keydata h4{margin:0 0 8px;font-size:13px;letter-spacing:.12em}
.kd{display:grid;grid-template-columns:1fr auto;gap:5px 10px;font-size:12.5px;font-variant-numeric:tabular-nums;margin:0}
.kd dt{color:var(--muted)} .kd dd{margin:0;text-align:right;font-weight:500}
.kd .sep{grid-column:1/-1;border-top:1px solid var(--line);margin:3px 0}
.pts{margin:0;padding:0;list-style:none;display:grid;gap:9px}
.pts li{padding-left:14px;position:relative}
.pts li::before{content:"";position:absolute;left:0;top:.62em;width:6px;height:6px;background:var(--kb)}
.pts b{display:block}
.tw{overflow-x:auto;margin:6px 0 12px}
table{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}
th{background:var(--ink);color:var(--paper);font-weight:500;padding:6px 8px;text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}
td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
table.raw td{white-space:normal;text-align:left;font-size:12px;border:1px solid var(--line);vertical-align:top}
table.raw tr:first-child td{background:var(--soft);font-weight:700}
tr.tot td{font-weight:700;background:var(--soft)}
td.l{text-align:left;white-space:normal}
.neg{color:var(--neg)}
.cap{font-size:11.5px;color:var(--muted);margin:-4px 0 12px}
.chart{width:100%;height:auto;display:block}
.chart .grid{stroke:var(--line);stroke-width:1} .chart .grid.zero{stroke:var(--muted)}
.chart .ax{fill:var(--muted);font-size:10.5px;font-family:var(--sans)} .chart .ax.op{fill:var(--kb)}
.chart .bar{fill:var(--barc)} .chart .bar.lossb{fill:var(--neg);opacity:.75} .chart .bar.est{fill:url(#hatch)} .chart .bar.prov{fill:var(--ink)}
.chart .hbg{fill:var(--soft)} .chart .hln{stroke:var(--kb);stroke-width:3}
.chart .opl{fill:none;stroke:var(--kb);stroke-width:2.5} .chart .opd{fill:var(--kb)}
.chart .vl{paint-order:stroke;stroke:var(--sheet);stroke-width:3px;fill:var(--ink);font-size:10.5px;font-weight:700;font-family:var(--sans)}
.chart .wick{stroke-width:1} .chart .wick.up{stroke:var(--up)} .chart .wick.dn{stroke:var(--dn)}
.chart .cdl.up{fill:var(--up)} .chart .cdl.dn{fill:var(--dn)} .chart .vol{opacity:.45} .chart .vol.up{fill:var(--up)} .chart .vol.dn{fill:var(--dn)}
.chart .ma{fill:none;stroke-width:1.4} .chart .ma20{stroke:var(--kb)} .chart .ma60{stroke:#3FA06B} .chart .ma120{stroke:#8E5BB5}
.chart .lastl{stroke:var(--ink);stroke-dasharray:3 3} .chart .lastb{fill:var(--ink)} .chart .lastt{fill:var(--sheet);font-size:11px;font-weight:700;font-family:var(--sans)}
.chart .fl{fill:none;stroke-width:2.2} .chart .fl-f{stroke:var(--f)} .chart .fl-i{stroke:var(--i)} .chart .fl-p{stroke:var(--p)}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--muted);margin:4px 0 2px}
.legend i{display:inline-block;width:10px;height:10px;margin-right:5px;vertical-align:-1px}
.mix{display:flex;height:16px;margin:8px 0 4px;overflow:hidden;border-radius:2px}
.box{background:var(--soft);padding:12px 14px;margin:8px 0 12px} .box p:last-child{margin:0}
.flag{border-left:5px solid var(--neg);background:#F6DCD5;color:#7A2A18;padding:10px 14px;margin:0 0 14px;font-weight:700}
.tag{display:inline-block;font-size:11.5px;font-weight:700;padding:1px 8px;border-radius:2px;white-space:nowrap}
.ok{background:#E3EFE7;color:#2F6B4F} .chk{background:#FBEFC9;color:#7A5600} .bad{background:#F6DCD5;color:#9A3420}
.disc{font-size:12px;color:var(--muted)} .disc p{margin:0 0 8px}
/* 인쇄·PDF: 배경색 유지, A4 세로 한 쪽 = 한 섹션, 화면용 여백·테두리 제거 */
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
@page{size:A4;margin:10mm 9mm}
@media print{
:root{--ink:#3D3A35!important;--paper:#FFFFFF!important;--sheet:#FFFFFF!important;--line:#E4DED2!important;--muted:#7A746A!important;--soft:#F6EFDC!important;
--neg:#B4442F!important;--barc:#C9C2B4!important;--up:#D2463C!important;--dn:#2F66B0!important;--f:#2F66B0!important;--p:#8C8577!important;color-scheme:light}
body{background:#fff;padding:0;font-size:10.5pt;line-height:1.55}
.page{max-width:none;margin:0;border:0;break-after:page;page-break-after:always}
.page:last-child{break-after:auto;page-break-after:auto}
.band,.keydata,.box,.flag,th,tr.tot td,table.raw tr:first-child td,.tag,.mix span,.legend i{-webkit-print-color-adjust:exact;print-color-adjust:exact}
.band{padding:6px 14px} .inner{padding:12px 4px 4px} .foot{margin:10px 4px 0}
h1{font-size:19pt} h2{font-size:13pt;break-after:avoid} h3{break-after:avoid}
.cover{grid-template-columns:minmax(0,1fr) 62mm;gap:6mm}
.chart,table,.box{break-inside:avoid}
.chart{max-height:95mm}
.tw{overflow:visible}
table{font-size:8.5pt} table.raw td{font-size:8pt}
}
@media (max-width:720px){.cover{grid-template-columns:1fr}.inner{padding:18px 16px 6px}.band{padding:8px 16px}.foot{margin:14px 16px 0;flex-direction:column;gap:2px}h1{font-size:21px}}
"""


def page(band_l: str, band_r: str, body: str, src: str, no: int) -> str:
    return (f'<section class="page"><div class="band"><span>{esc(band_l)}</span><span>{esc(band_r)}</span></div>'
            f'<div class="inner">{body}</div><div class="foot"><span><b>자료:</b> {src}</span><span>{no}</span></div></section>')


def eok(v, d: int = 0) -> str:
    if v is None:
        return "데이터 없음"
    return f"{v / 10000:,.2f}조원" if abs(v) >= 10000 else f"{v:,.{d}f}억원"


def render(D: dict) -> str:
    name, code = D["name"], D["code"]
    mk = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}.get(D["market"], "기타")
    acc = D["co"].get("acc_mt") or "12"
    qs, ann, prov, est, mkt = D["qs"], D["ann"], D["prov"], D["est"], D["mkt"]
    stamp = TODAY.strftime("%Y.%m.%d")
    tag = f"{name} ({code})"
    dsrc = lambda *r: ", ".join(x for x in r if x)
    ar, lr = D["annual_rep"], D["latest_rep"]

    # 필터
    last4 = [r["op"] for r in qs if r["op"] is not None][-4:]
    loss4 = len(last4) == 4 and all(v < 0 for v in last4)
    mcap = D["price"] * D["shares"]["issued"] / 1e8 if D["price"] and D["shares"] else None
    pbr = mcap / D["equity"] if mcap and D["equity"] and D["equity"] > 0 else None
    bps = D["equity"] * 1e8 / (D["shares"]["issued"] - D["shares"]["treasury"]) if D["equity"] and D["shares"] and D["shares"]["issued"] > D["shares"]["treasury"] else None
    flags = []
    if loss4:
        flags.append("최근 4개 분기 연속 영업적자")
    if pbr and pbr >= 25:
        flags.append(f"PBR {pbr:.1f}배 (25배 이상)")
    ttm = sum(last4) if len(last4) == 4 else None
    live_d = [d for d in D["decs"] if d["live"]]
    mz_live = [m for m in D["mezz"] if not m["none"]]
    mezz_line = ("미상환 표에 잔액 있음: " + ", ".join(sorted({m['kind'] for m in mz_live}))) if mz_live else (
        f"최근 5년 발행결정 {len(D['decs'])}건 중 만기 전 {len(live_d)}건 (상환·전환 여부 확인 필요)" if live_d else "최근 5년 CB·BW·EB 발행결정 없음")

    # 표지
    pv = ""
    if prov:
        if prov["turn"]:
            pv = f"{prov['period']} 영업이익 {eok(prov['op'])}, 전분기 적자에서 흑자전환"
        else:
            pv = f"{prov['period']} 영업이익 {eok(prov['op'])}, 전분기 대비 {prov['chg']:+.1f}%" if prov.get("chg") is not None else f"{prov['period']} 영업이익 {eok(prov['op'])}"
    title = f"{name}, {pv}" if pv else f"{name} 실적카드 요약"
    biz = D["biz"] or {}
    one = re.split(r"(?<=다\.)\s", biz.get("overview") or "")[0][:220] if biz.get("overview") else "사업보고서 원문을 받지 못했습니다 (데이터 없음)."
    mix_s = ", ".join(f"{l[:18]} {v:.0f}%" for l, v in sorted(biz.get("mix") or [], key=lambda t: -t[1])[:3])
    bullets = []
    if prov:
        s = f"매출 {eok(prov['sales'])}" if prov.get("sales") is not None else ""
        bullets.append(f"<li><b>이번 잠정실적: {esc(pv)}</b>{esc(s)}{' · 전년 동기 대비 ' + format(prov['yoy'], '+.1f') + '%' if prov.get('yoy') is not None else ''}. "
                       f"잠정실적 공시 접수번호 {esc(prov['rcp'])}.</li>")
    bullets.append(f"<li><b>주요 사업</b>{esc(one)}{' 매출 구성(최근 사업연도): ' + esc(mix_s) + '.' if mix_s else ''}</li>")
    if est:
        bullets.append(f"<li><b>{D['cur_year']}E 영업이익 {eok(est['op'])} (산출값, 컨센서스 아님)</b>{esc(est['base'])} 실적 + {esc(est['how'])}.</li>")
    bullets.append(f"<li><b>메자닌: {esc(mezz_line)}</b>자세한 표는 4쪽에 있습니다.</li>")
    bars = mkt.get("bars") or []
    hi52 = max((b["h"] or b["c"]) for b in bars[-250:]) if bars else None
    lo52 = min((b["l"] or b["c"]) for b in bars[-250:]) if bars else None
    chg = None
    q = mkt.get("quote") or {}
    if q.get("price") and q.get("prev"):
        chg = (q["price"] / q["prev"] - 1) * 100
    elif len(bars) >= 2:
        chg = (bars[-1]["c"] / bars[-2]["c"] - 1) * 100
    lq = D["lq"]
    lqs = f"{lq[0]}.{lq[1] * 3:02d}" if lq else "-"
    kd = [("현재가", f"{D['price']:,.0f}원" if D["price"] else "데이터 없음"), ("전일 대비", f"{chg:+.2f}%" if chg is not None else "-"),
          ("52주 최고 / 최저", f"{hi52:,.0f} / {lo52:,.0f}" if hi52 else "데이터 없음"), ("시가총액", eok(mcap) if mcap else "데이터 없음"),
          ("발행주식수(보통주)", f"{D['shares']['issued']:,.0f}주" if D["shares"] else "데이터 없음"), None,
          (f"지배주주지분 ({lqs})", eok(D["equity"])), ("BPS", f"{bps:,.0f}원" if bps else "데이터 없음"),
          ("PBR (후행)", f"{pbr:.2f}배" if pbr else "데이터 없음"), ("영업이익 (최근 4분기)", eok(ttm) if ttm is not None else "데이터 없음"),
          None, ("미상환 메자닌", "있음" if mz_live else ("확인 필요" if live_d else "없음")),
          ("필터 판정", "제외 대상" if flags else "통과")]
    kd_html = "".join('<div class="sep"></div>' if r is None else f"<dt>{esc(r[0])}</dt><dd>{esc(r[1])}</dd>" for r in kd)
    flag_html = f'<div class="flag">프로젝트 필터 제외 대상: {esc(" · ".join(flags))}</div>' if flags else ""
    chart1 = (svg_candles(bars) + '<div class="legend"><span><i style="background:var(--up)"></i>상승</span><span><i style="background:var(--dn)"></i>하락</span>'
              '<span><i style="background:var(--kb)"></i>MA20</span><span><i style="background:#3FA06B"></i>MA60</span><span><i style="background:#8E5BB5"></i>MA120</span></div>'
              ) if len(bars) >= 20 else '<div class="box"><p>일봉 데이터 없음 (KB API·네이버 모두 조회 실패)</p></div>'
    ma_txt = ""
    if len(bars) >= 120:
        cl = [b["c"] for b in bars]
        ma_txt = " · ".join(f"MA{n} {sum(cl[-n:]) / n:,.0f}원({(cl[-1] / (sum(cl[-n:]) / n) - 1) * 100:+.1f}%)" for n in (20, 60, 120))
    cover = (f'{flag_html}<div class="cover"><div><div class="kicker">{esc(tag)} · {mk} · {esc(D["co"].get("induty_code") and "업종코드 " + D["co"]["induty_code"] or "")} · {acc}월 결산</div>'
             f'<h1>{esc(title)}</h1><p class="cap" style="margin:4px 0 14px">기준일 {TODAY:%Y-%m-%d} · 결산월 {acc}월 · 재무 연결(CFS) 우선, 없으면 별도(OFS) 표기 · 실적카드 자동 요약판</p>'
             f'<ul class="pts">{"".join(bullets)}</ul></div>'
             f'<aside class="keydata" aria-label="KEY DATA"><h4>KEY DATA</h4><dl class="kd">{kd_html}</dl>'
             f'<p class="cap" style="margin:10px 0 0">주가: {esc(mkt.get("bars_src") or "데이터 없음")}. 주식수·지분: OpenDART {esc(lr["report_nm"] if lr else "-")} '
             f'{esc(lr["rcept_no"] if lr else "")}. PBR = 시가총액 ÷ 지배주주지분.</p></aside></div>'
             f'<h2 style="margin-top:18px">① 일봉 차트 (최근 1년)</h2>{chart1}<p class="cap">{esc(ma_txt)}{" · " if ma_txt else ""}{esc(mkt.get("bars_src") or "")}</p>')
    pages = [page("KB증권 제주지점 · 실적카드 요약", stamp, cover,
                  dsrc(esc(mkt.get("bars_src") or ""), f"OpenDART({esc(lr['rcept_no']) if lr else '-'})", f"잠정실적 {esc(prov['rcp'])}" if prov else ""), 1)]

    # ② 수급
    fl = mkt.get("flows") or []
    if fl:
        fl = [r for r in fl if r["d"] >= (TODAY - timedelta(days=92)).strftime("%Y%m%d")]
    if len(fl) >= 5:
        unit = mkt.get("flow_unit") or ""
        def sums(rows):
            return [sum(r[k] or 0 for r in rows) for k in ("f", "i", "p")]
        rows = [("최근 5거래일", sums(fl[-5:])), ("최근 20거래일", sums(fl[-20:])), (f"3개월 ({len(fl)}거래일)", sums(fl))]
        trs = "".join(f"<tr><td>{esc(l)}</td>" + "".join(f"<td>{fnum(v)}</td>" for v in s) + "</tr>" for l, s in rows)
        recent = "".join(f"<tr><td>{r['d'][4:6]}/{r['d'][6:]}</td><td>{fnum(r['f'])}</td><td>{fnum(r['i'])}</td><td>{fnum(r['p'])}</td></tr>" for r in reversed(fl[-10:]))
        body2 = (f'<h2>② 최근 3개월 수급</h2>{svg_flows(fl)}<div class="legend"><span><i style="background:var(--f)"></i>외국인</span>'
                 f'<span><i style="background:var(--i)"></i>기관</span><span><i style="background:var(--p)"></i>개인</span></div>'
                 f'<p class="cap">순매수 누적 ({esc(unit)}) · {esc(mkt.get("flows_src") or "")}</p>'
                 f'<h3>구간별 순매수 합계 ({esc(unit)})</h3><div class="tw"><table><tr><th>구간</th><th>외국인</th><th>기관</th><th>개인</th></tr>{trs}</table></div>'
                 f'<h3>최근 10거래일</h3><div class="tw"><table><tr><th>일자</th><th>외국인</th><th>기관</th><th>개인</th></tr>{recent}</table></div>')
    else:
        body2 = '<h2>② 최근 3개월 수급</h2><div class="box"><p>투자자별 매매동향 데이터 없음 (KB API·네이버 모두 조회 실패)</p></div>'
    pages.append(page("가격·수급", tag, body2, esc(mkt.get("flows_src") or "데이터 없음"), 2))

    # ③ 10년 + ④ 분기
    years = sorted(ann)[-10:]
    items = [{"lab": f"{y % 100:02d}", "op": ann[y]["op"], "opm": (ann[y]["op"] / ann[y]["sales"] * 100) if ann[y]["sales"] else None,
              "kind": ""} for y in years]
    if est:
        items.append({"lab": f"{D['cur_year'] % 100:02d}", "op": est["op"], "opm": est["op"] / est["sales"] * 100 if est.get("sales") else None, "kind": "est"})
    ofs = [y for y in years if ann[y]["fs"] == "OFS"]
    atr = "".join(f"<tr><td>{y}{' (별도)' if ann[y]['fs'] == 'OFS' else ''}</td><td>{fnum(ann[y]['sales'])}</td><td>{fnum(ann[y]['op'])}</td>"
                  f"<td>{fnum(ann[y]['op'] / ann[y]['sales'] * 100 if ann[y]['sales'] else None, 1)}</td></tr>" for y in years)
    if est:
        atr += (f'<tr class="tot"><td>{D["cur_year"]}E (산출값)</td><td>{fnum(est.get("sales"))}</td><td>{fnum(est["op"])}</td>'
                f'<td>{fnum(est["op"] / est["sales"] * 100 if est.get("sales") else None, 1)}</td></tr>')
    qitems = [{"lab": f"{r['y'] % 100:02d}.{r['q']}Q", "op": r["op"], "opm": r["op"] / r["sales"] * 100 if r["op"] is not None and r["sales"] else None,
               "kind": "prov" if r.get("prov") else ("calc" if r["calc"] else "")} for r in qs]
    qtr = "".join(f"<tr><td>{r['y']}.{r['q']}Q{' 잠정' if r.get('prov') else (' 산출' if r['calc'] else '')}</td><td>{fnum(r['sales'])}</td><td>{fnum(r['op'])}</td>"
                  f"<td>{fnum(r['op'] / r['sales'] * 100 if r['op'] is not None and r['sales'] else None, 1)}</td><td class='l'>{esc(r['rcp'] or '')}</td></tr>" for r in qs)
    est_note = (f"{D['cur_year']}E는 컨센서스가 아니라 산출값입니다: {esc(est['base'])} 실적 + {esc(est['how'])}." if est else "")
    body3 = (f'<h2>③ 10년 영업이익 · 영업이익률</h2>{svg_combo(items, "연간 영업이익과 영업이익률")}'
             f'<div class="legend"><span><i style="background:var(--barc)"></i>영업이익(억원, 왼쪽)</span><span><i style="background:var(--kb)"></i>영업이익률(%, 오른쪽)</span>'
             f'<span><i style="background:repeating-linear-gradient(45deg,var(--kb) 0 2px,var(--soft) 2px 5px)"></i>E = 산출 추정치</span></div>'
             f'<p class="cap">{est_note} {"별도 기준 연도: " + ", ".join(map(str, ofs)) + "." if ofs else ""}</p>'
             f'<div class="tw"><table><tr><th>연도</th><th>매출액(억원)</th><th>영업이익(억원)</th><th>영업이익률(%)</th></tr>{atr}</table></div>'
             f'<h2 style="margin-top:18px">④ 최근 3년 분기 영업이익</h2>{svg_combo(qitems, "분기 영업이익과 영업이익률")}'
             f'<div class="legend"><span><i style="background:var(--ink)"></i>P = 이번 잠정실적</span><span>* = 4분기 산출값(연간 − 3분기 누적)</span></div>'
             f'<div class="tw"><table><tr><th>분기</th><th>매출액(억원)</th><th>영업이익(억원)</th><th>이익률(%)</th><th class="l">접수번호</th></tr>{qtr}</table></div>')
    reps = sorted({ann[y]["rcp"] for y in years if ann[y].get("rcp")})
    pages.append(page("실적 추이", tag, body3, f"OpenDART 주요계정(fnlttSinglAcnt), 사업보고서 {esc(', '.join(reps[-4:]))} 외 분기·반기보고서, 잠정실적 공시", 3))

    # ⑤ 메자닌
    parts = []
    for m in D["mezz"]:
        st = '<span class="tag ok">미상환 없음</span>' if m["none"] else '<span class="tag chk">미상환 있음</span>'
        parts.append(f"<h3>{esc(m['kind'])} {st}</h3>" + (table_html(m["table"], 60) if m["table"] and not m["none"] else "")
                     + "".join(f'<p class="cap">{esc(n)}</p>' for n in m["notes"]))
    if not D["mezz"]:
        parts.append('<div class="box"><p>정기보고서에서 미상환 메자닌 표를 찾지 못했습니다 (데이터 없음). 아래 발행결정 공시로 확인하세요.</p></div>')
    if D["decs"]:
        dr = "".join(f"<tr><td>{esc(d['kind'])} {esc(d['tm'] or '')}회</td><td>{esc(d['bddd'] or '')}</td><td>{fnum(d['fta'])}</td><td>{esc(d['mtd'] or '-')}</td>"
                     f"<td>{esc(d['prc'])}</td><td class='l'>{esc(d['period'])}</td><td>{esc(d['floor'])}</td><td>{esc(d['vs'])}</td>"
                     f"<td>{'<span class=\"tag chk\">만기 전</span>' if d['live'] else '만기 경과'}</td><td class='l'>{esc(d['rcp'])}</td></tr>" for d in D["decs"])
        parts.append(f'<h3>최근 5년 발행결정 (주요사항보고서)</h3><div class="tw"><table><tr><th>구분</th><th>결정일</th><th>권면총액(억원)</th><th>만기</th>'
                     f'<th>전환·행사가</th><th class="l">청구기간</th><th>리픽싱 하한</th><th>주식수 대비(%)</th><th>상태</th><th class="l">접수번호</th></tr>{dr}</table></div>'
                     '<p class="cap">발행결정 기준입니다. 이후 전환·조기상환·만기전 취득으로 잔액이 줄었을 수 있어, 위 정기보고서 미상환 표와 아래 공시로 확인합니다.</p>')
    else:
        parts.append('<p>최근 5년 CB·BW·EB 발행결정 공시: 없음 (OpenDART 주요사항보고서 조회).</p>')
    if D["mdisc"]:
        parts.append('<h3>최근 1년 관련 공시</h3><div class="tw"><table><tr><th>접수일</th><th class="l">공시</th><th class="l">접수번호</th></tr>'
                     + "".join(f"<tr><td>{esc(r['dt'])}</td><td class='l'>{esc(r['t'])}</td><td class='l'>{esc(r['rcp'])}</td></tr>" for r in D["mdisc"][:15])
                     + "</table></div>")
    pages.append(page("희석 리스크: 미상환 CB·BW·EB", tag, "<h2>⑤ 미상환 전환사채·신주인수권부사채·교환사채</h2>" + "".join(parts),
                      f"OpenDART {esc(lr['report_nm'] if lr else '-')} {esc(lr['rcept_no'] if lr else '')} '미상환 발행현황', 주요사항보고서(cvbd·bdwt·exbdIsDecsn), 공시목록", 4))

    # ⑥ 사업
    if biz:
        cust = "".join(f'<p>{esc(t)}</p>' if k == "p" else table_html(t, 60) for k, t in biz.get("customers") or [])
        body6 = (f'<h2>⑥ 주요 사업 · 매출 구성 · 주요 매출처</h2><p class="cap">출처: {esc(ar["report_nm"])} (접수번호 {esc(ar["rcept_no"])}) II. 사업의 내용 원문 발췌. '
                 f'요약이 아닌 원문 문장이며, 긴 문단은 앞부분만 실었습니다.</p>'
                 f'<h3>사업의 개요</h3><div class="box"><p>{esc(biz.get("overview") or "데이터 없음")}</p></div>'
                 f'<h3>주요 제품·서비스별 매출 구성</h3>{mix_bar(biz.get("mix") or [])}'
                 f'{table_html(biz["products"], 70) if biz.get("products") else "<p>표를 찾지 못했습니다 (데이터 없음).</p>"}'
                 f'<h3>주요 매출처</h3>{cust or "<p>사업보고서에서 주요 매출처 문단을 찾지 못했습니다 (데이터 없음).</p>"}'
                 f'{"<h3>수주 현황</h3>" + table_html(biz["backlog"], 50) if biz.get("backlog") else ""}')
    else:
        body6 = '<h2>⑥ 주요 사업 · 매출 구성 · 주요 매출처</h2><div class="box"><p>최근 2년 사업보고서를 찾지 못했습니다 (데이터 없음).</p></div>'
    pages.append(page("사업 구조", tag, body6, f"OpenDART 사업보고서 원문 {esc(ar['rcept_no']) if ar else '-'}", 5))

    disc = ('<p>본 자료는 실적카드(전분기 대비 영업이익 급증 잠정실적 공시)가 나간 종목에 대해 자동으로 만든 요약 자료이며, 매수·매도 추천이 아닙니다. '
            '투자에 대한 최종 결정과 책임은 투자자 본인에게 있습니다.</p>'
            '<p>재무 수치는 금융감독원 OpenDART API의 회사 공시(연결 우선, 연결이 없으면 별도로 표기)를 따랐고 각 표에 접수번호를 적었습니다. '
            '분기 손익은 3개월 금액이며, 4분기 단독 실적은 연간에서 3분기 누적을 뺀 산출값입니다. 이번 분기는 잠정실적 공시 수치이며 확정 실적과 다를 수 있습니다.</p>'
            '<p>올해 추정치(E)는 컨센서스가 아니라 본문에 적은 방식으로 과거 실적에서 계산한 산출값입니다. PBR은 현재가 × 보통주 발행주식수를 최근 정기보고서의 지배주주지분으로 나눈 후행 값입니다.</p>'
            '<p>주가·수급은 KB증권 Open API를 우선 사용하고, 조회에 실패하면 네이버 증권을 2차 출처로 쓰고 그 사실을 표기했습니다. '
            '사업의 개요·주요 매출처는 사업보고서 원문 발췌이며 해석을 더하지 않았습니다.</p>'
            f'<p>본 자료는 KB증권 리서치센터의 공식 발간물이 아니며, 작성 시점({TODAY:%Y-%m-%d}) 이후 공시와 시장 상황에 따라 내용이 달라질 수 있습니다.</p>')
    pages.append(page("고지사항", tag, f'<div class="disc">{disc}</div>', "OpenDART, KB증권 Open API, 네이버 증권(2차 출처)", 6))
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(name)} 실적카드 요약</title><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700;900&display=swap">'
            f'<style>{CSS}</style></head><body>{"".join(pages)}</body></html>')


# ------------------------------------------------------------------ 발송
def telegram_document(path: Path, caption: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("  텔레그램 설정 없음 → 발송 생략")
        return
    bd = uuid.uuid4().hex
    parts = []
    for k, v in (("chat_id", chat), ("caption", caption[:1000])):
        parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    fname = urllib.parse.quote(path.name)
    parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="document"; filename="{fname}"; filename*=UTF-8\'\'{fname}\r\n'
                 f'Content-Type: {"application/pdf" if path.suffix == ".pdf" else "text/html"}\r\n\r\n'.encode() + path.read_bytes() + b"\r\n")
    parts.append(f"--{bd}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendDocument", data=b"".join(parts),
                                 headers={"Content-Type": f"multipart/form-data; boundary={bd}"})
    r = urllib.request.urlopen(req, timeout=60)
    log("  텔레그램 파일:", r.status)


def to_pdf(path: Path) -> Path | None:
    """같은 HTML을 A4 PDF로 (배경색 포함). 휴대폰 텔레그램에서 바로 열린다."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("  playwright 없음 → PDF 생략")
        return None
    pdf = path.with_suffix(".pdf")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(color_scheme="light")
        pg.goto(path.resolve().as_uri(), wait_until="networkidle")
        pg.emulate_media(media="print")
        pg.pdf(path=str(pdf), format="A4", print_background=True, prefer_css_page_size=True)
        b.close()
    return pdf


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    send = "--send" in sys.argv
    if "--market-dump" in sys.argv:  # 시험용: 시세·수급만 받아 out/market/<code>.json
        d = ROOT / "out" / "market"
        d.mkdir(parents=True, exist_ok=True)
        for tok in re.split(r"[,\s]+", " ".join(args)):
            code, _, mk = tok.partition(":")
            if code:
                (d / f"{code}.json").write_text(json.dumps(market_data(code, mk or "Y"), ensure_ascii=False), encoding="utf-8")
        return
    items = []
    for a in args:
        for tok in re.split(r"[,\s]+", a):
            if tok:
                code, _, rcp = tok.partition(":")
                items.append((code.strip(), rcp.strip() or None))
    if not items:
        print(__doc__)
        return
    cmap = corp_map()
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    for code, rcp in items:
        if code not in cmap:
            log("종목코드 없음:", code)
            continue
        try:
            D = build(code, rcp, cmap)
            path = OUT / f"{TODAY:%Y%m%d}_{code}_{re.sub(r'[^0-9A-Za-z가-힣]+', '', D['name'])}.html"
            path.write_text(render(D), encoding="utf-8")
            log("  저장:", path)
            made.append(str(path.relative_to(ROOT)))
            try:
                pdf = to_pdf(path)
            except Exception as e:
                log("  PDF 실패:", type(e).__name__, e)
                pdf = None
            if pdf:
                made.append(str(pdf.relative_to(ROOT)))
            if send:
                cap = f"📑 실적카드 요약 보고서 · {D['name']}({code})\n" + (f"{D['prov']['period']} 잠정실적 기준" if D["prov"] else "")
                try:
                    telegram_document(pdf or path, cap)
                except Exception as e:
                    log("  텔레그램 실패:", type(e).__name__, e)
        except Exception as e:
            import traceback
            traceback.print_exc()
            log("보고서 실패:", code, type(e).__name__, e)
    (OUT / "made.json").write_text(json.dumps(made, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
