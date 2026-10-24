"""거래대금 상위 종목 (코스피·코스닥 각 10종목, ETF·ETN 제외).

네이버 증권 모바일 API:
    GET https://m.stock.naver.com/api/stocks/priceTop/{KOSPI|KOSDAQ}?page=1&pageSize=N   거래대금 내림차순
    accumulatedTradingValue 는 백만원 단위.
"""

from __future__ import annotations

import asyncio
import time

import httpx

URL = "https://m.stock.naver.com/api/stocks/priceTop/{mk}?page=1&pageSize=40"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://m.stock.naver.com/",
}
TTL = 120
_cache: tuple[float, dict] | None = None


def _f(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


async def _one(http: httpx.AsyncClient, mk: str, n: int) -> list[dict]:
    r = await http.get(URL.format(mk=mk))
    r.raise_for_status()
    out = []
    for s in r.json().get("stocks") or []:
        if s.get("stockEndType") != "stock":  # ETF·ETN 은 뺀다
            continue
        value = _f(s.get("accumulatedTradingValue"))
        out.append({"name": s.get("stockName"), "code": s.get("itemCode"), "price": _f(s.get("closePrice")),
                    "change_pct": _f(s.get("fluctuationsRatio")), "value_eok": round(value / 100) if value is not None else None,
                    "time": s.get("localTradedAt")})
        if len(out) >= n:
            break
    return out


async def top_value(n: int = 10) -> dict:
    global _cache
    if _cache and time.time() - _cache[0] < TTL:
        return _cache[1]
    async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as http:
        kospi, kosdaq = await asyncio.gather(_one(http, "KOSPI", n), _one(http, "KOSDAQ", n))
    data = {"source": "naver", "kospi": kospi, "kosdaq": kosdaq,
            "time": max((s["time"] or "" for s in kospi + kosdaq), default="")}
    _cache = (time.time(), data)
    return data
