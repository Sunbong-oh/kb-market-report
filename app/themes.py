"""오늘의 강세 테마 - 네이버 금융(증권) 테마 등락률 상위.

네이버 증권 모바일 API (finance.naver.com/sise/theme.naver 가 옮겨 간 곳):
    GET https://m.stock.naver.com/api/stocks/theme?page=1&pageSize=N        테마 목록 (등락률 내림차순)
    GET https://m.stock.naver.com/api/stocks/theme/{no}?page=1&pageSize=M   테마 소속 종목

테마 '설명글'(themeDescription)은 네이버의 저작물이라 가져다 쓰지 않고,
수치(상승·하락 종목수, 주도주 등락률)만으로 요약한다.
네이버 조회가 실패하면(클라우드에서 차단 등) KB 업종랭킹(IVM30010)으로 대신한다.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from . import market
from .kb_client import KBClient

BASE = "https://m.stock.naver.com/api/stocks/theme"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Referer": "https://m.stock.naver.com/",
}
_cache: tuple[float, dict] | None = None
TTL = 120


def _f(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _stock_pct(s: dict) -> float | None:
    pct = _f(s.get("fluctuationsRatio"))
    if pct is not None and pct > 0 and (s.get("compareToPreviousPrice") or {}).get("name") in ("FALLING", "LOWER_LIMIT"):
        pct = -pct
    return pct


async def _naver_themes(count: int, leaders: int) -> list[dict]:
    async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as http:
        res = await http.get(BASE, params={"page": 1, "pageSize": count})
        res.raise_for_status()
        groups = res.json().get("groups", [])[:count]

        async def detail(g: dict) -> dict:
            stocks: list[dict] = []
            try:
                r = await http.get(f"{BASE}/{g['no']}", params={"page": 1, "pageSize": 100})
                r.raise_for_status()
                rows = [{"name": s.get("stockName"), "code": s.get("itemCode"), "change_pct": _stock_pct(s)}
                        for s in r.json().get("stocks", [])]
                rows = [s for s in rows if s["change_pct"] is not None]
                stocks = sorted(rows, key=lambda s: s["change_pct"], reverse=True)[:leaders]
            except Exception:
                pass  # 주도주를 못 가져와도 테마 등락률은 보여준다
            return {
                "name": g.get("name"),
                "change_pct": _f(g.get("changeRate")),
                "total": g.get("totalCount"),
                "rise": g.get("riseCount"),
                "fall": g.get("fallCount"),
                "steady": g.get("steadyCount"),
                "leaders": stocks,
                "url": f"https://stock.naver.com/market/stock/kr/theme/{g['no']}" if g.get("no") else None,
            }

        return list(await asyncio.gather(*(detail(g) for g in groups)))


async def top_themes(kb: KBClient, count: int = 5, leaders: int = 3) -> dict:
    """{"source": "naver" | "kb", "themes": [{name, change_pct, total, rise, fall, steady, leaders[]}]}"""
    global _cache
    if _cache and time.time() - _cache[0] < TTL:
        return _cache[1]
    try:
        themes = await _naver_themes(count, leaders)
        if not themes:
            raise RuntimeError("테마 목록이 비어 있음")
        result = {"source": "naver", "themes": themes}
    except Exception:
        # 대체: KB 업종랭킹 (테마 분류는 아니지만 강세 업종)
        s = await market.sector_top(kb)
        rows = [{"name": f"{label} {r['name']}", "change_pct": r["change_pct"], "leaders": []}
                for label, key in (("코스피", "kospi"), ("코스닥", "kosdaq")) for r in s.get(key, [])]
        rows.sort(key=lambda r: r["change_pct"] or 0, reverse=True)
        result = {"source": "kb", "themes": rows[:count]}
    _cache = (time.time(), result)
    return result
