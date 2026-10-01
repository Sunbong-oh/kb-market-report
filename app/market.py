"""투자정보 TR 호출 + 응답 정리.

필드명은 kb-openapi 저장소의 samples.generated.json(outputSpec) 기준이다.
반복 레코드(out2, out5 …)의 실제 JSON 키 이름은 명세에 없으므로 특정 키 이름에
의존하지 않고, 해당 레코드에만 있는 필드(예: invstr_clsf_nm)를 가진 dict를
dataBody 안에서 찾아 쓴다.
"""

from __future__ import annotations

import re
from typing import Any

from .kb_client import KBClient

# 전일대비구분코드: 1 상한, 2 상승, 3 보합, 4 하한, 5 하락
DOWN_CODES = {"4", "5"}
_SCALE_RE = re.compile(r"_p(\d)(?:_\d+)?$")


# ---------------------------------------------------------------- helpers
def find_records(obj: Any, key: str) -> list[dict]:
    found: list[dict] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if key in o:
                found.append(o)
            for v in o.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(o, list):
            for item in o:
                walk(item)

    walk(obj)
    return found


def num(rec: dict, field: str) -> float | int | None:
    """문자열 숫자를 변환한다. 소수점 없는 정수가 오면 필드명 접미사(_p2 등)로 자릿수를 보정."""
    v = rec.get(field)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        if "." in s:
            return float(s)
        n = int(s)
    except ValueError:
        return None
    m = _SCALE_RE.search(field)
    return n / 10 ** int(m.group(1)) if m else n


def signed(value: float | int | None, ccd: Any) -> float | int | None:
    if value is None:
        return None
    if str(ccd).strip() in DOWN_CODES and value > 0:
        return -value
    return value


def text(rec: dict, field: str) -> str:
    return str(rec.get(field) or "").strip()


# ------------------------------------------------------------------ 현재가
async def quote(kb: KBClient, code: str) -> dict:
    """IVU10140 주식현재가."""
    d = await kb.call("IVU10140", {"excg_clsf": "1", "shrt_cd": code})
    rec = (find_records(d, "now_prc") or [d])[0]
    ccd = rec.get("bdy_cmpr_ccd")
    price = num(rec, "now_prc")
    prev_close = num(rec, "bdy_cls_prc") or num(rec, "sprc")
    # 가격(정수)으로 직접 등락률을 계산해 스케일 해석 오류를 피한다
    if price and prev_close:
        change = price - prev_close
        change_pct = round(change / prev_close * 100, 2)
    else:
        change = signed(num(rec, "bdy_cmpr"), ccd)
        change_pct = signed(num(rec, "up_dwn_r_p2"), ccd)
    return {
        "code": code,
        "name": text(rec, "is_nm") or code,
        "market": text(rec, "mkt_clsf_nm"),
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "prev_close": prev_close,
        "open": num(rec, "opn_prc"),
        "high": num(rec, "hgh_prc"),
        "low": num(rec, "lw_prc"),
        "upper_limit": num(rec, "ulmt_prc"),
        "lower_limit": num(rec, "llmt_prc"),
        "volume": num(rec, "acml_vlm"),
        "ask1": num(rec, "s_sq1_askprc"),
        "bid1": num(rec, "b_sq1_askprc"),
        "foreign_ratio": num(rec, "fgnr_hld_sgrvt_p2"),
        "per": num(rec, "per_p2"),
        "pbr": num(rec, "pbr_p2"),
        "market_cap": num(rec, "opn_prc_tl_amt"),
        "high_250": num(rec, "dy250_max_prc"),
        "low_250": num(rec, "dy250_min_prc"),
        "open_time": text(rec, "opn_prc_tm"),
    }


# ------------------------------------------------------- 시장종합 (IVSA0070)
MAIN_INDICES = ("KOSPI종합", "KOSDAQ종합")
OVERSEAS_INDICES = ("CME@NQ",)  # 나스닥100 선물
# IVS11560 통합차트의 지수 분봉 조회 조건 (indx_id → mkt_clsf): 5 KOSPI업종, 6 KOSDAQ업종
INDEX_CHART_MARKET = {"KGG01P": "5", "QGG01P": "6"}
async def market_summary(kb: KBClient) -> dict:
    """지수, 프로그램매매(차익/비차익), 투자자별 순매수(외국인/기관/개인)를 한 번에."""
    d = await kb.call("IVSA0070")
    top = (find_records(d, "mprft_nt_b") or [d])[0]

    # out2 국내 지수 중 코스피·코스닥 종합만 + out4 해외 지수 중 나스닥 선물
    indices = [
        {
            "id": text(r, "indx_id"),
            "name": text(r, "indx_nm").replace("종합", ""),
            "value": num(r, "now_indx_p2"),
            "change": signed(num(r, "bdy_cmpr_p2"), r.get("bdy_cmpr_ccd")),
            "change_pct": signed(num(r, "up_dwn_r_p2"), r.get("bdy_cmpr_ccd")),
        }
        for r in find_records(d, "indx_nm")
        if text(r, "indx_nm") in MAIN_INDICES
    ]
    indices += [
        {
            "id": text(r, "symbl_cd"),
            "name": text(r, "symbl_nm"),
            "value": num(r, "now_prc"),
            "change": signed(num(r, "bdy_cmpr_p2"), r.get("bdy_cmpr_ccd")),
            "change_pct": signed(num(r, "up_dwn_r_p2"), r.get("bdy_cmpr_ccd")),
        }
        for r in find_records(d, "symbl_cd")
        if text(r, "symbl_cd") in OVERSEAS_INDICES
    ]
    # KOSPI200 최근월 선물도 지수 카드로 (수치만, 미니차트 없음)
    fut = front_futures(d)
    if fut:
        indices.append({"id": fut["code"], "name": "KOSPI200 선물", "value": fut["price"],
                        "change": fut["change"], "change_pct": fut["change_pct"]})

    investors = [
        {
            "code": text(r, "invstr_cd"),
            "name": text(r, "invstr_clsf_nm"),
            "kospi": num(r, "kspi_nt_b"),
            "kosdaq": num(r, "ksdq_nt_b"),
            "futures": num(r, "fts_nt_b"),
            "call": num(r, "call_opt_nt_b"),
            "put": num(r, "put_opt_nt_b"),
        }
        for r in find_records(d, "invstr_clsf_nm")
    ]

    arb = num(top, "mprft_nt_b")
    non_arb = num(top, "nmp_nt_b")
    return {
        "time": text(top, "inq_dy_tm"),
        "indices": indices,
        "program": {
            "arbitrage": arb,
            "non_arbitrage": non_arb,
            "total": (arb or 0) + (non_arb or 0) if arb is not None or non_arb is not None else None,
        },
        "investors": investors,
        "futures": front_futures(d),
        "breadth": {
            "kospi": {k: num(top, f"kspi_{k}_is_c") for k in ("ulmt", "up", "unchng", "dwn", "llmt")},
            "kosdaq": {k: num(top, f"ksdq_{k}_is_c") for k in ("ulmt", "up", "unchng", "dwn", "llmt")},
        },
    }


# ------------------------------------------------------------- 선물
def front_futures(summary: dict) -> dict | None:
    """IVSA0070 out3(선물·옵션 시세)에서 KOSPI200 최근월 선물("F 202612")을 고른다."""
    for r in find_records(summary, "nstmt_agr_q"):
        if text(r, "is_cd") and text(r, "is_nm").startswith("F"):
            ccd = r.get("bdy_cmpr_ccd")
            return {
                "code": text(r, "is_cd"),
                "name": "KOSPI200 선물 " + text(r, "is_nm")[1:].strip(),
                "price": num(r, "now_prc_p2"),
                "change": signed(num(r, "bdy_cmpr_p2"), ccd),
                "change_pct": signed(num(r, "up_dwn_r_p2"), ccd),
                "volume": num(r, "vlm"),
                "open_interest": num(r, "nstmt_agr_q"),
            }
    return None


def investor_futures(summary: dict) -> dict[str, float | None]:
    """IVSA0070 out5 투자자별 KOSPI200 선물 순매수(당일 누적, 억원) {"외국인": -5961, …}."""
    return {
        text(r, "invstr_clsf_nm"): num(r, "fts_nt_b")
        for r in find_records(summary, "invstr_clsf_nm")
        if text(r, "invstr_clsf_nm")
    }


async def futures_minute(kb: KBClient, code: str, minutes: int = 1) -> list[dict]:
    """IVS11560 통합차트 - KOSPI200 선물(mkt_clsf 2) 분봉, 가장 최근 거래일 주간장(08:45~15:45)만."""
    d = await kb.call(
        "IVS11560",
        {"info_ccd": "1", "mkt_clsf": "2", "chrt_clsf": "B", "minute_tck_indx": str(minutes), "is_cd": code,
         "inq_clsf": "2", "strt_dy": "", "inq_cnt": str(460 // minutes + 20)},
        ttl=20,
    )
    recs = find_records(d, "cls_prc_p2")
    day = [r for r in recs if "084500" <= text(r, "tm").zfill(6) <= "154500"]
    if not day:
        return []
    last = max(text(r, "dt") for r in day)
    bars = [
        {
            "t": text(r, "tm").zfill(6)[:4],
            "o": num(r, "opn_prc_p2"),
            "h": num(r, "hgh_prc_p2"),
            "l": num(r, "lw_prc_p2"),
            "c": num(r, "cls_prc_p2"),
            "v": num(r, "vlm"),
            "oi": num(r, "nstmt_agr_q"),
        }
        for r in day
        if text(r, "dt") == last
    ]
    return sorted(bars, key=lambda b: b["t"])


def overseas_values(summary: dict) -> dict[str, float | None]:
    """IVSA0070 out4에서 해외지수(나스닥 선물 등) 현재값 {"CME@NQ": 30625.25}."""
    return {
        text(r, "symbl_cd"): num(r, "now_prc")
        for r in find_records(summary, "symbl_cd")
        if text(r, "symbl_cd") in OVERSEAS_INDICES
    }


async def index_minute(kb: KBClient, index_id: str) -> dict:
    """IVS11560 통합차트 - 코스피/코스닥 지수 1분봉, 가장 최근 거래일 정규장(09:00~15:30)."""
    d = await kb.call(
        "IVS11560",
        {"info_ccd": "1", "mkt_clsf": INDEX_CHART_MARKET[index_id], "chrt_clsf": "B", "minute_tck_indx": "1",
         "is_cd": index_id, "inq_clsf": "2", "strt_dy": "", "inq_cnt": "420"},
        ttl=30,
    )
    recs = [r for r in find_records(d, "cls_prc_p2") if "090000" <= text(r, "tm").zfill(6) <= "153000"]
    if not recs:
        return {"prev_close": None, "points": []}
    last = max(text(r, "dt") for r in recs)
    day = [r for r in recs if text(r, "dt") == last]
    points = sorted(({"t": text(r, "tm").zfill(6)[:4], "c": num(r, "cls_prc_p2")} for r in day), key=lambda p: p["t"])
    return {"prev_close": num(day[0], "bdy_cls_prc_p2"), "points": points}


# 업종랭킹에 섞여 오는 '업종이 아닌 지수'(시장 전체·규모별·소속부)는 뺀다
NOT_SECTOR = re.compile(r"종합|대형주|중형주|소형주|글로벌|기업부|우량|벤처|중견")


async def sector_top(kb: KBClient) -> dict:
    """IVM30010 업종랭킹 - 코스피·코스닥 상승률 상위 업종 (오늘의 강세 업종)."""
    out = {}
    for key, mk, prefix in (("kospi", "1", "코스피"), ("kosdaq", "2", "코스닥")):
        d = await kb.call("IVM30010", {"mkt_clsf": mk}, ttl=60)
        out[key] = [
            {
                "name": text(r, "indx_nm").removeprefix(prefix).strip(),
                "value": num(r, "now_indx_p2"),
                "change": signed(num(r, "bdy_cmpr_p2"), r.get("bdy_cmpr_ccd")),
                "change_pct": signed(num(r, "up_dwn_r_p2"), r.get("bdy_cmpr_ccd")),
            }
            for r in find_records(d, "indx_nm")
            if text(r, "indx_nm") and not NOT_SECTOR.search(text(r, "indx_nm"))
        ]
    return out


# ---------------------------------------------------------- 프로그램매매
async def program_by_stock(kb: KBClient, code: str, count: int = 10) -> list[dict]:
    """IVU10450 종목별프로그램매매추이 (시간별, 금액)."""
    d = await kb.call(
        "IVU10450",
        {"excg_clsf": "1", "is_cd": code, "amt_q_clsf": "1", "prd_clsf": "1", "inq_cnt": str(count)},
    )
    return [
        {
            "date": text(r, "dt"),
            "time": text(r, "tm"),
            "price": num(r, "now_prc"),
            "change_pct": signed(num(r, "up_dwn_r_p2"), r.get("bdy_cmpr_ccd")),
            "net_buy": num(r, "nt_b_amt"),
            "net_buy_delta": num(r, "nt_b_amt_incrs_dcrs"),
        }
        for r in find_records(d, "nt_b_amt")
    ]


# --------------------------------------------------------- 환율 / 금리
async def usd_krw(kb: KBClient) -> dict | None:
    """IVA60190 환율종합 → 원/달러만 (crncy_cd 예: "USDKRWSMBS")."""
    d = await kb.call("IVA60190", ttl=60)
    for r in find_records(d, "crncy_cd"):
        if text(r, "crncy_cd").upper().startswith("USDKRW"):
            ccd = r.get("bdy_cmpr_ccd")
            return {
                "rate": num(r, "cls_prc_p4"),
                "change": signed(num(r, "bdy_cmpr_p4"), ccd),
                "change_pct": signed(num(r, "bdy_cmpr_r_p2"), ccd),
                "time": text(r, "fnl_rcp_tm"),
            }
    return None


async def treasury_10y(kb: KBClient) -> dict | None:
    """IVA10370 증시주변자금동향 → 국고채 10년물 금리."""
    d = await kb.call("IVA10370", ttl=60)
    recs = find_records(d, "ntnbnd_yr10_thng_yld_p5")
    if not recs:
        return None
    r = recs[0]
    return {
        "date": text(r, "dt2") or text(r, "dt"),
        "yield": num(r, "ntnbnd_yr10_thng_yld_p5"),
        "change": num(r, "ntnbnd_yr10_thng_yld_cmpr_p5"),
    }

