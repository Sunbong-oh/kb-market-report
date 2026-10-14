"""미국 데일리 '증시 캘린더' 항목에 예상치·실제치를 붙인다 (dashboard.py).

- 경제지표: 발표 전에는 ForexFactory 주간 일정의 예상·이전, 발표 후에는 나스닥 경제지표 일정의 실제·예상·이전
  (인베스팅닷컴은 GitHub 서버에서 403으로 막혀 쓰지 못한다)
- 미국 실적: 나스닥 실적 일정의 EPS 예상(발표 후엔 실제·서프라이즈)
네트워크가 안 되거나 짝을 못 찾으면 원래 문구 그대로 둔다. kb-market-report/calendar_values.py 와 같은 규칙.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import date, timedelta

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
      "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}
FF_URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
NQ_ECON = "https://api.nasdaq.com/api/calendar/economicevents?date={}"
NQ_EARN = "https://api.nasdaq.com/api/calendar/earnings?date={}"

# (한글 키워드들, [(표시 이름, ForexFactory 제목, 나스닥 이름)]) — 첫 키워드가 지표 이름
MACRO = (
    (("PCE", "근원 PCE"), [("", "Core PCE Price Index m/m", "Core PCE Price Index")]),
    (("CPI", "소비자물가"), [("", "CPI y/y", "CPI"), ("근원", "Core CPI y/y", "Core CPI")]),
    (("PPI", "생산자물가"), [("", "PPI m/m", "PPI"), ("근원", "Core PPI m/m", "Core PPI")]),
    (("소매판매",), [("", "Retail Sales m/m", "Retail Sales")]),
    (("고용", "비농업"), [("", "Non-Farm Employment Change", "Nonfarm Payrolls"), ("실업률", "Unemployment Rate", "Unemployment Rate")]),
    (("금리결정", "금리 결정"), [("", "Federal Funds Rate", "Fed Interest Rate Decision")]),
    (("산업생산",), [("", "Industrial Production m/m", "Industrial Production")]),
    (("GDP",), [("", "Advance GDP q/q", "GDP")]),
)
TICKERS = {
    "마이크론": "MU", "TSMC": "TSM", "테슬라": "TSLA", "알파벳": "GOOGL", "구글": "GOOGL", "마이크로소프트": "MSFT",
    "메타": "META", "애플": "AAPL", "아마존": "AMZN", "엔비디아": "NVDA", "넷플릭스": "NFLX", "브로드컴": "AVGO",
    "AMD": "AMD", "인텔": "INTC", "퀄컴": "QCOM", "오라클": "ORCL", "ASML": "ASML", "팔란티어": "PLTR",
    "어도비": "ADBE", "세일즈포스": "CRM", "코스트코": "COST", "나이키": "NKE", "JP모건": "JPM", "골드만삭스": "GS",
    "모건스탠리": "MS", "BoA": "BAC", "씨티": "C", "웰스파고": "WFC", "램리서치": "LRCX", "KLA": "KLAC",
}
_cache: dict[str, object] = {}


def _get(url: str):
    if url not in _cache:
        try:
            _cache[url] = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=10).read().decode("utf-8"))
        except Exception as e:
            print("캘린더 수치 조회 실패:", url, type(e).__name__, e)
            _cache[url] = None
    return _cache[url]


def _clean(v) -> str:
    return (v or "").replace("&nbsp;", "").strip()


def _macro(d: date, text: str) -> str:
    if not re.match(r"★?\s*미", text) and "FOMC" not in text:
        return ""
    groups = [(keys[0], s) for keys, s in MACRO if any(k in text for k in keys)]
    if not groups:
        return ""
    series = [((name + " " + label).strip() if len(groups) > 1 and not label else label, ff_t, nq_n)
              for name, s in groups for label, ff_t, nq_n in s]
    days = {d.isoformat(), (d - timedelta(days=1)).isoformat()}
    ff = [e for u in FF_URLS for e in (_get(u) or []) if e.get("country") == "USD" and (e.get("date") or "")[:10] in days]
    # 나스닥 경제지표 일정은 미국 발표가 하루 뒤 날짜에 실리기도 한다 (9월 CPI 10/14 발표 → 10/15 목록) → 앞뒤 하루까지 본다
    nq_days = [(d + timedelta(days=k)).isoformat() for k in (0, 1, -1)]
    nq = [r for day in nq_days for r in (((_get(NQ_ECON.format(day)) or {}).get("data") or {}).get("rows") or [])
          if r.get("country") == "United States"]
    parts, used = [], set()
    for label, ff_title, nq_name in series:
        e = next((x for x in ff if x.get("title") == ff_title), None)
        fc, prev = (_clean(e.get("forecast")), _clean(e.get("previous"))) if e else ("", "")
        # 나스닥은 같은 이름(전월비·전년비)이 여러 줄 → ForexFactory '이전' 값으로 짝을 맞춘다. 없으면 첫 줄
        cands = [r for r in nq if _clean(r.get("eventName")) == nq_name and _clean(r.get("actual")) and id(r) not in used]
        r = next((x for x in cands if prev and _clean(x.get("previous")) == prev), None) or (cands[0] if cands and not e else None)
        if r:
            used.add(id(r))
            act, fc, prev = _clean(r.get("actual")), _clean(r.get("consensus")) or fc, _clean(r.get("previous")) or prev
            s = f"실제 {act}" + (f", 예상 {fc}" if fc else "") + (f", 이전 {prev}" if prev else "")
        elif fc or prev:
            s = ", ".join(x for x in (f"예상 {fc}" if fc else "", f"이전 {prev}" if prev else "") if x)
        else:
            continue
        parts.append(f"{label} {s}".strip())
    return " / ".join(parts)


def _earnings(d: date, text: str) -> str:
    name = re.sub(r"\(.*?\)|실적", "", text.lstrip("★")).strip()
    sym = TICKERS.get(name)
    if not sym:
        return ""
    for day in (d, d - timedelta(days=1), d + timedelta(days=1)):
        rows = ((_get(NQ_EARN.format(day.isoformat())) or {}).get("data") or {}).get("rows") or []
        r = next((x for x in rows if x.get("symbol") == sym), None)
        if not r:
            continue
        fc, eps, sp = _clean(r.get("epsForecast")), _clean(r.get("eps")), _clean(r.get("surprise"))
        if eps:
            s = f"EPS 실제 {eps}" + (f", 예상 {fc}" if fc else "")
            try:
                s += f", {float(sp):+.1f}%"
            except ValueError:
                pass
            return s
        return f"EPS 예상 {fc}" if fc else ""
    return ""


def values(d: date, item: str) -> str:
    """항목에 붙일 수치 문자열 (없으면 빈 문자열). d = 미국 기준 발표일."""
    try:
        return _macro(d, item) or (_earnings(d, item) if "실적" in item or item.lstrip("★") in TICKERS else "")
    except Exception as e:
        print("캘린더 수치 붙이기 실패:", item, type(e).__name__, e)
        return ""
