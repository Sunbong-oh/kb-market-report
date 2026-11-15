"""미국 빅테크(M7) + S&P500 밸류에이션 6개월 추이 데이터 만들기.

- 주가: Yahoo Finance 일별 종가 (분할 반영)
- 재무: SEC EDGAR XBRL companyfacts (10-Q·10-K, 공시일 기준으로만 반영 → 그 날 알 수 있던 숫자)
- S&P500: ^GSPC 일별 종가 + multpl.com 의 S&P500 12개월 EPS(월) · 주당순자산(분기)

출력: valuation/bigtech_valuation.json, valuation/bigtech_valuation.html (template.html 에 데이터를 넣은 단일 파일)
"""

from __future__ import annotations

import json
import re
import html as htmlmod
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "valuation"
UA = {"User-Agent": "kb-market-report research script"}

TICKERS = {  # 티커: (CIK, 한글명)
    "AAPL": (320193, "애플"),
    "MSFT": (789019, "마이크로소프트"),
    "GOOGL": (1652044, "알파벳"),
    "AMZN": (1018724, "아마존"),
    "META": (1326801, "메타"),
    "NVDA": (1045810, "엔비디아"),
    "TSLA": (1318605, "테슬라"),
}

REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
NI_TAGS = ["NetIncomeLoss"]
EPS_TAGS = ["EarningsPerShareDiluted"]
OPINC_TAGS = ["OperatingIncomeLoss"]
PRETAX_TAGS = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
               "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
TAX_TAGS = ["IncomeTaxExpenseBenefit"]
EQUITY_TAGS = ["StockholdersEquity"]
CASH_TAGS = ["CashAndCashEquivalentsAtCarryingValue"]
DEBT_TAGS = ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtCurrent", "CommercialPaper",
             "ShortTermBorrowings", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligations",
             "LongTermDebtAndCapitalLeaseObligationsCurrent", "FinanceLeaseLiabilityNoncurrent"]


def d(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------- SEC
def sec_facts(cik: int) -> dict:
    r = httpx.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=UA, timeout=60)
    r.raise_for_status()
    return r.json()["facts"]


def _entries(facts: dict, tag: str, unit: str | None = None) -> list[dict]:
    for ns in ("us-gaap", "dei"):
        node = facts.get(ns, {}).get(tag)
        if node:
            units = node["units"]
            u = unit if unit in units else next(iter(units))
            return [e for e in units[u] if e.get("form") in ("10-Q", "10-K", "10-Q/A", "10-K/A")]
    return []


def quarterly_flow(facts: dict, tags: list[str], unit: str = "USD", restated: bool = False) -> list[dict]:
    """분기 단위 흐름 값 [{end, filed, val}] — 4분기(Q4)는 연간 − 9개월 누적으로 구한다.
    restated=True 면 값은 가장 나중 공시(주식분할 소급 반영)를, 반영 시점은 최초 공시일을 쓴다 (EPS용)."""
    merged: dict[date, dict] = {}
    for tag in tags:
        es = _entries(facts, tag, unit)
        if not es:
            continue
        q: dict[date, dict] = {}   # 분기(약 90일)
        ytd9: dict[date, dict] = {}
        fy: dict[date, dict] = {}
        for e in es:
            if "start" not in e:
                continue
            s, en = d(e["start"]), d(e["end"])
            days = (en - s).days
            filed = d(e["filed"])
            bucket = q if 80 <= days <= 100 else ytd9 if 260 <= days <= 285 else fy if 350 <= days <= 380 else None
            if bucket is None:
                continue
            prev = bucket.get(en)
            if prev is None:
                bucket[en] = {"start": s, "end": en, "filed": filed, "val": e["val"], "_last": filed}
            else:
                if restated and filed >= prev["_last"]:
                    prev["val"], prev["_last"] = e["val"], filed
                if filed < prev["filed"]:   # 처음 공시된 날 = 시장이 알게 된 날
                    prev["filed"] = filed
                    if not restated:
                        prev["val"] = e["val"]
        for en, f in fy.items():
            if any(abs((en - k).days) <= 5 for k in q):
                continue
            nine = next((v for k, v in ytd9.items() if v["start"] == f["start"] or abs((v["start"] - f["start"]).days) <= 5), None)
            if nine:
                q[en] = {"start": nine["end"], "end": en, "filed": f["filed"], "val": f["val"] - nine["val"]}
        for en, v in q.items():   # 앞 순서 태그 우선, 빈 분기만 다음 태그로 채움
            if not any(abs((en - k).days) <= 5 for k in merged):
                merged[en] = v
    return sorted(merged.values(), key=lambda x: x["end"])


def instant(facts: dict, tags: list[str], unit: str = "USD") -> list[dict]:
    """재무상태표(시점) 값 [{end, filed, val}], 태그별로 합치지 않고 가장 최근 데이터가 있는 태그 사용."""
    best: list[dict] = []
    for tag in tags:
        es = _entries(facts, tag, unit)
        m: dict[date, dict] = {}
        for e in es:
            if "start" in e:
                continue
            en, filed = d(e["end"]), d(e["filed"])
            if en not in m or filed < m[en]["filed"]:
                m[en] = {"end": en, "filed": filed, "val": e["val"]}
        rows = sorted(m.values(), key=lambda x: x["end"])
        if rows and (not best or rows[-1]["end"] > best[-1]["end"]):
            best = rows
    return best


def total_debt(facts: dict) -> dict[date, float]:
    """기준일별 총차입금: 장기(유동+비유동) + 단기차입·CP. 같은 기준일에 있는 태그만 더한다."""
    by_tag = {t: {r["end"]: r["val"] for r in instant(facts, [t])} for t in DEBT_TAGS}
    out: dict[date, float] = {}
    ends = set().union(*[set(v) for v in by_tag.values()]) if by_tag else set()
    for en in ends:
        g = lambda t: by_tag[t].get(en)
        if g("LongTermDebt") is not None:
            lt = g("LongTermDebt")
        elif g("LongTermDebtAndCapitalLeaseObligations") is not None:
            lt = g("LongTermDebtAndCapitalLeaseObligations") + (g("LongTermDebtAndCapitalLeaseObligationsCurrent") or 0)
        else:
            lt = (g("LongTermDebtNoncurrent") or 0) + (g("LongTermDebtCurrent") or 0)
        st = (g("CommercialPaper") or 0) + (g("ShortTermBorrowings") or 0)
        out[en] = lt + st
    return out


def asof(rows: list[dict], day: date, n: int = 1) -> list[dict]:
    known = [r for r in rows if r["filed"] <= day]
    return known[-n:] if len(known) >= n else []


def ttm(rows: list[dict], day: date, lag: int = 0) -> float | None:
    """day 시점까지 공시된 최근 4개 분기 합 (lag=4 → 그 직전 4개 분기 합)."""
    known = [r for r in rows if r["filed"] <= day]
    if len(known) < 4 + lag:
        return None
    sel = known[len(known) - 4 - lag: len(known) - lag]
    if (sel[-1]["end"] - sel[0]["end"]).days > 300:   # 분기 누락 → 계산 안 함
        return None
    return sum(r["val"] for r in sel)


# ---------------------------------------------------------------- 가격
def yahoo(symbol: str, start: date, end: date) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 86400
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
                  params={"period1": p1, "period2": p2, "interval": "1d", "events": "split"},
                  headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts, closes = res["timestamp"], res["indicators"]["quote"][0]["close"]
    off = res["meta"].get("gmtoffset", 0)
    px = [(datetime.fromtimestamp(t + off, tz=timezone.utc).date(), c) for t, c in zip(ts, closes) if c is not None]
    splits = [(datetime.fromtimestamp(int(k), tz=timezone.utc).date(), v["numerator"] / v["denominator"])
              for k, v in (res.get("events", {}).get("splits") or {}).items()]
    return px, splits


# ---------------------------------------------------------------- S&P500 (multpl)
def multpl(path: str) -> list[tuple[date, float]]:
    t = httpx.get(f"https://www.multpl.com/{path}", headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        cells = [htmlmod.unescape(re.sub("<[^>]+>", "", c)).replace("†", "").strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 2:
            try:
                out.append((datetime.strptime(cells[0], "%b %d, %Y").date(), float(cells[1].replace(",", "").replace("%", ""))))
            except ValueError:
                pass
    return sorted(out)


def r2(x):
    return None if x is None else round(x, 2)


def main() -> None:
    end = date.today()
    start = end - timedelta(days=183)
    # 재무는 과거 2년치 공시가 필요하지만 가격은 6개월
    result = {"generated": datetime.now(timezone.utc).isoformat(timespec="minutes"), "start": start.isoformat(),
              "series": {}, "notes": {}}

    for tk, (cik, name) in TICKERS.items():
        print("…", tk)
        f = sec_facts(cik)
        rev, ni = quarterly_flow(f, REV_TAGS), quarterly_flow(f, NI_TAGS)
        eps = quarterly_flow(f, EPS_TAGS, "USD/shares", restated=True)
        opi, pre, tax = quarterly_flow(f, OPINC_TAGS), quarterly_flow(f, PRETAX_TAGS), quarterly_flow(f, TAX_TAGS)
        eq, cash = instant(f, EQUITY_TAGS), instant(f, CASH_TAGS)
        debt = total_debt(f)
        shares = instant(f, ["EntityCommonStockSharesOutstanding"], "shares")
        if not shares:  # 알파벳처럼 표지(dei) 주식수가 없는 회사는 재무상태표 유통주식수 사용
            shares = instant(f, ["CommonStockSharesOutstanding"], "shares")
        if not shares:  # 메타처럼 둘 다 없으면 분기 희석 가중평균 주식수
            m: dict[date, dict] = {}
            for e in _entries(f, "WeightedAverageNumberOfDilutedSharesOutstanding", "shares"):
                if "start" in e and 80 <= (d(e["end"]) - d(e["start"])).days <= 100:
                    en = d(e["end"])
                    if en not in m or d(e["filed"]) < m[en]["filed"]:
                        m[en] = {"end": en, "filed": d(e["filed"]), "val": e["val"]}
            shares = sorted(m.values(), key=lambda x: x["end"])

        px, splits = yahoo(tk, start, end)
        rows = []
        for day, close in px:
            sh = asof(shares, day)
            if not sh:
                continue
            nshare = sh[-1]["val"]
            for sd, ratio in splits:          # 공시 이후 분할이 있었다면 주식수 보정
                if sh[-1]["end"] < sd <= day:
                    nshare *= ratio
            mcap = close * nshare
            ni4, ni4p = ttm(ni, day), ttm(ni, day, 4)
            rv4, rv4p = ttm(rev, day), ttm(rev, day, 4)
            e4, e4p = ttm(eps, day), ttm(eps, day, 4)
            eq_now, eq_prev = asof(eq, day), asof(eq, day, 5)
            per = mcap / ni4 if ni4 and ni4 > 0 else None
            pbr = mcap / eq_now[-1]["val"] if eq_now else None
            psr = mcap / rv4 if rv4 else None
            avg_eq = (eq_prev[0]["val"] + eq_prev[-1]["val"]) / 2 if eq_prev else (eq_now[-1]["val"] if eq_now else None)
            roe = ni4 / avg_eq * 100 if ni4 is not None and avg_eq else None
            # EPS 성장률은 희석 EPS 기준 (자사주 매입 효과 반영)
            g = (e4 / e4p - 1) * 100 if e4 and e4p and e4p > 0 else None
            peg = per / g if per and g and g > 0 else None
            rev_g = (rv4 / rv4p - 1) * 100 if rv4 and rv4p else None
            # ROIC = TTM 영업이익 × (1 − 실효세율) / (자기자본 + 총차입금 − 현금성자산), 기말·전년 동기말 평균
            roic = None
            o4, p4, t4 = ttm(opi, day), ttm(pre, day), ttm(tax, day)
            if o4 is not None and p4 and t4 is not None and eq_prev:
                rate = min(max(t4 / p4, 0), 0.35)
                def ic(eqrow):
                    en = eqrow["end"]
                    c = next((r["val"] for r in cash if r["end"] == en), 0)
                    return eqrow["val"] + debt.get(en, 0) - c
                avg_ic = (ic(eq_prev[0]) + ic(eq_prev[-1])) / 2
                if avg_ic > 0:
                    roic = o4 * (1 - rate) / avg_ic * 100
            rows.append({"d": day.isoformat(), "px": r2(close), "mcap": round(mcap / 1e9, 1), "per": r2(per),
                         "pbr": r2(pbr), "psr": r2(psr), "roe": r2(roe), "roic": r2(roic), "peg": r2(peg),
                         "epsg": r2(g), "revg": r2(rev_g)})
        last_q = asof(ni, end)[-1]["end"].isoformat() if asof(ni, end) else None
        result["series"][tk] = {"name": name, "rows": rows, "lastQuarter": last_q}
        result["notes"][tk] = f"최근 반영 분기 말 {last_q}"

    # ---------------- S&P500
    print("… S&P500")
    px, _ = yahoo("^GSPC", start, end)
    eps = multpl("s-p-500-earnings/table/by-month")
    bv = multpl("s-p-500-book-value/table/by-quarter")
    rows = []
    for day, close in px:
        # multpl 표는 월말 기준 값 → 실제 집계 공표까지 약 3개월 걸리므로 날짜 당일까지 '표에 있는' 마지막 값 사용
        e_now = [v for dt, v in eps if dt <= day]
        b_now = [v for dt, v in bv if dt <= day]
        if not e_now or not b_now:
            continue
        e = e_now[-1]
        e_prev_list = [v for dt, v in eps if dt <= day - timedelta(days=365)]
        b_prev_list = [v for dt, v in bv if dt <= day - timedelta(days=365)]
        per, pbr = close / e, close / b_now[-1]
        g = (e / e_prev_list[-1] - 1) * 100 if e_prev_list else None
        roe = e / ((b_now[-1] + b_prev_list[-1]) / 2) * 100 if b_prev_list else None
        rows.append({"d": day.isoformat(), "px": r2(close), "mcap": None, "per": r2(per), "pbr": r2(pbr), "psr": None,
                     "roe": r2(roe), "roic": None, "peg": r2(per / g) if g and g > 0 else None, "epsg": r2(g), "revg": None})
    result["series"]["SPX"] = {"name": "S&P500", "rows": rows,
                               "lastQuarter": f"EPS {eps[-1][0]} · BPS {bv[-1][0]}"}

    OUT.mkdir(exist_ok=True)
    (OUT / "bigtech_valuation.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    tpl = (OUT / "template.html").read_text(encoding="utf-8")
    page = tpl.replace("/*DATA*/null", json.dumps(result, ensure_ascii=False))
    (OUT / "bigtech_valuation.html").write_text(page, encoding="utf-8")
    print("saved", OUT / "bigtech_valuation.json", OUT / "bigtech_valuation.html")


if __name__ == "__main__":
    main()
