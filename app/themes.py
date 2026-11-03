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
import html
import re
import time
from datetime import datetime, timedelta, timezone

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


# ---- 테마가 오른 이유: 그날 뉴스 제목 한 줄 (주도주 종목 뉴스 + 네이버 뉴스 검색 '○○주', 최근 하루) ----
# 테마 이름이 들어 있고 '…기대·수혜·소식에·급락에' 같은 원인 표현이 있는 제목을 고른다. 증시 시황 기사는 뺀다.
SEARCH = "https://m.search.naver.com/search.naver"
CAUSE = re.compile(r"기대|수혜|소식|우려|논의|급락에|급등에|영향|효과|호재|덕분|발표|계약|수주|승인|정책|인수|유출|해킹|합병|통합|분쟁|지분|경쟁|급증|인상|인하|규제|지원")
NOISE = re.compile(r"시황|증시|월가|뉴욕|나스닥|코스피|코스닥|마감|\[이 시각|올댓차이나|잠든 사이")


def _kw(name: str) -> list[str]:
    """테마 이름 → 제목에서 찾을 말: '항공/저가 항공사(LCC)' → ['항공', '저가 항공사', 'LCC']."""
    out = []
    for t in re.split(r"[/(),·]", name or ""):
        t = re.sub(r"\s*등$", "", t.strip())
        t = re.sub(r"(관련주|주|업)$", "", t) if len(t) > 2 else t
        if len(t) >= 2 and t not in out:
            out.append(t)
    return out


async def _search_titles(http: httpx.AsyncClient, q: str) -> list[tuple[str, str]]:
    try:
        r = await http.get(SEARCH, params={"where": "m_news", "sort": "1", "query": q, "nso": "so:dd,p:1d"},
                           headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"})
        h = r.text
    except Exception:
        return []
    return [(html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip(), html.unescape(m.group(1)))
            for m in re.finditer(r'<a [^>]*href="(https?://[^"]+)"[^>]*>\s*<span class="sds-comps-text[^"]*headline1[^"]*">(.*?)</span>', h, re.S)]


async def _reason(http: httpx.AsyncClient, name: str, stocks: list[dict]) -> list[dict]:
    """이유 한두 줄 [{t, u}] (서로 다른 이야기면 두 개까지). 못 찾으면 빈 목록."""
    kws = _kw(name)
    if not kws:
        return []
    cands: dict[str, list] = {}  # 제목 → [링크, 주도주 몇 곳 뉴스에 있었나]
    for q in (kws[0] if kws[0].endswith("주") else kws[0] + "주", kws[0] + " 관련주"):
        for t, u in await _search_titles(http, q):
            cands.setdefault(t, [u, 0])
    today = datetime.now(KST).strftime("%Y%m%d")
    for st in stocks[:3]:
        try:
            r = await http.get(NEWS.format(st["code"]), params={"page": 1, "pageSize": 8})
            data = r.json()
        except Exception:
            continue
        rows = []
        for g in data if isinstance(data, list) else [data]:
            if isinstance(g, dict):
                rows += g.get("items") or ([g] if "title" in g else [])
        for it in rows:
            t = html.unescape(re.sub(r"<[^>]+>", "", str(it.get("title") or ""))).strip()
            if t and str(it.get("datetime", "")).startswith(today):
                link = (f"https://n.news.naver.com/mnews/article/{it['officeId']}/{it['articleId']}"
                        if it.get("officeId") and it.get("articleId") else "")
                c = cands.setdefault(t, [link, 0])
                c[1] += 1

    def score(t: str, n: int) -> int:
        sc = (3 if any(k in t for k in kws) else 0) + min(2, len(CAUSE.findall(t))) + (1 if n >= 2 else 0)
        return sc - (4 if NOISE.search(t) else 0)
    ranked = sorted(((score(t, v[1]), t, v[0]) for t, v in cands.items()), key=lambda x: -x[0])
    words = lambda t: set(re.findall(r"[가-힣A-Za-z]{2,}", t))
    out = []
    for sc, t, u in ranked:
        if sc < 4 or len(out) >= 2:
            break
        if any(len(words(t) & words(o["full"])) >= 2 for o in out):  # 같은 이야기면 하나만
            continue
        out.append({"t": re.sub(r"^\s*(\[[^\]]*\]\s*)+", "", t).strip(), "u": u or None, "full": t})
    return [{"t": o["t"], "u": o["u"]} for o in out]


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
            try:
                why = await _reason(http, g.get("name") or "", stocks)
            except Exception:
                why = []
            return {
                "name": g.get("name"),
                "change_pct": _f(g.get("changeRate")),
                "total": g.get("totalCount"),
                "rise": g.get("riseCount"),
                "fall": g.get("fallCount"),
                "steady": g.get("steadyCount"),
                "leaders": stocks,
                "url": f"https://stock.naver.com/market/stock/kr/theme/{g['no']}" if g.get("no") else None,
                "reason": why,
            }

        return list(await asyncio.gather(*(detail(g) for g in groups)))


# ---- 이슈 테마: 네이버 고정 테마 목록에 없는 그날의 재료(예: '호남 반도체 클러스터')를 잡는다 ----
# 오늘 급등주들의 네이버 종목 뉴스에서 '같은 기사'가 여러 종목에 함께 묶여 있으면 하나의 이슈로 본다.
UP = "https://m.stock.naver.com/api/stocks/up/{}"
NEWS = "https://m.stock.naver.com/api/news/stock/{}"
KST = timezone(timedelta(hours=9))
SKIP_NAME = re.compile(r"ETN|ETF|인버스|레버리지|스팩|KODEX|TIGER|RISE|ACE|SOL|PLUS|HANARO|KOSEF")
SKIP_TITLE = re.compile(r"시황|마감|서울데이터랩|오늘의 메모|공시|장 초반|거래상위|상승률 1위|코스피\s?[\d,]+|코스닥\s?[\d,]+")
MIN_PCT, MIN_CAP = 7.0, 300  # 등락률 %, 시가총액(억원)


def _label(title: str) -> str:
    """기사 제목에서 이슈 이름: 따옴표 안 말을 우선, 없으면 '…' 앞부분."""
    for q in re.findall(r"['‘\"“]([^'’\"”]{4,24})['’\"”]", title):
        if not re.search(r"상한가|불기둥|폭등|급등|이 종목", q):
            return q.strip()
    head = re.split(r"[…·,]|\.\.\.", title)[0]
    head = re.sub(r"\[[^\]]*\]", "", head).strip()
    return head[:24]


async def _issues(http: httpx.AsyncClient, limit: int = 3) -> list[dict]:
    today = datetime.now(KST).strftime("%Y%m%d")
    risers = []
    for mk in ("KOSPI", "KOSDAQ"):
        try:
            r = await http.get(UP.format(mk), params={"page": 1, "pageSize": 40})
            r.raise_for_status()
            for s in r.json().get("stocks", []):
                pct, cap = _stock_pct(s), _f(s.get("marketValue"))
                if pct is not None and pct >= MIN_PCT and (cap or 0) >= MIN_CAP and not SKIP_NAME.search(s.get("stockName", "")):
                    risers.append({"name": s.get("stockName"), "code": s.get("itemCode"), "change_pct": pct})
        except Exception:
            continue
    sem = asyncio.Semaphore(8)

    async def news(st: dict) -> list[tuple[str, str]]:
        async with sem:
            try:
                r = await http.get(NEWS.format(st["code"]), params={"page": 1, "pageSize": 10})
                data = r.json()
            except Exception:
                return []
        rows = []
        for g in data if isinstance(data, list) else [data]:
            if isinstance(g, dict):
                rows += g.get("items") or ([g] if "title" in g else [])
        out = []
        for it in rows:
            t = html.unescape(re.sub(r"<[^>]+>", "", str(it.get("title") or ""))).strip()
            if t and str(it.get("datetime", "")).startswith(today) and not SKIP_TITLE.search(t):
                link = (f"https://n.news.naver.com/mnews/article/{it['officeId']}/{it['articleId']}"
                        if it.get("officeId") and it.get("articleId") else "")
                out.append((t, link))
        return out

    heads = await asyncio.gather(*(news(s) for s in risers))
    by_title: dict[str, list[int]] = {}
    links: dict[str, str] = {}
    for i, hs in enumerate(heads):
        for t, link in hs:
            by_title.setdefault(t, [])
            if i not in by_title[t]:
                by_title[t].append(i)
            links.setdefault(t, link)
    # 같은 기사로 묶인 종목끼리 합친다 (union-find)
    parent = list(range(len(risers)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for t, idx in by_title.items():
        if 2 <= len(idx) <= 8:
            for j in idx[1:]:
                parent[find(j)] = find(idx[0])
    groups: dict[int, list[int]] = {}
    for i in range(len(risers)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        shared = [(len(set(idx) & set(members)), t) for t, idx in by_title.items() if len(set(idx) & set(members)) >= 2]
        if not shared:
            continue
        shared.sort(key=lambda x: (-x[0], len(x[1])))
        title = shared[0][1]
        stocks = sorted((risers[i] for i in members), key=lambda s: s["change_pct"], reverse=True)
        out.append({"name": _label(title), "headline": title, "url": links.get(title) or None,
                    "change_pct": round(sum(s["change_pct"] for s in stocks) / len(stocks), 2),
                    "stocks": stocks[:6]})
    out.sort(key=lambda g: (len(g["stocks"]), g["change_pct"]), reverse=True)
    return out[:limit]


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
        try:
            async with httpx.AsyncClient(headers=HEADERS, timeout=10, follow_redirects=True) as http:
                result["issues"] = await _issues(http)
        except Exception:
            result["issues"] = []
    except Exception:
        # 대체: KB 업종랭킹 (테마 분류는 아니지만 강세 업종)
        s = await market.sector_top(kb)
        rows = [{"name": f"{label} {r['name']}", "change_pct": r["change_pct"], "leaders": []}
                for label, key in (("코스피", "kospi"), ("코스닥", "kosdaq")) for r in s.get(key, [])]
        rows.sort(key=lambda r: r["change_pct"] or 0, reverse=True)
        result = {"source": "kb", "themes": rows[:count]}
    _cache = (time.time(), result)
    return result
