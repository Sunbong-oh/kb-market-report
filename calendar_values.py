"""증시 캘린더 ★ 핵심 항목에 예상치·발표치를 붙인다.

- 미국 경제지표: ForexFactory 주간 일정(예상·이전) + 나스닥 경제지표 일정(발표치)
  (인베스팅닷컴은 GitHub 서버에서 403으로 막혀 쓰지 못한다)
- 미국 실적: 나스닥 실적 일정(EPS 예상·발표·서프라이즈)
네트워크가 안 되거나 짝을 못 찾으면 원래 문구를 그대로 둔다.
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import date, datetime, timedelta

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
FF_URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json", "https://nfs.faireconomy.media/ff_calendar_nextweek.json")
NQ_ECON = "https://api.nasdaq.com/api/calendar/economicevents?date={}"
NQ_EARN = "https://api.nasdaq.com/api/calendar/earnings?date={}"

# (한글 키워드들, [(표시 이름, ForexFactory 제목, 나스닥 이름)])
MACRO = (
    (("PCE", "근원 PCE"), [("", "Core PCE Price Index m/m", "Core PCE Price Index")]),
    (("CPI", "소비자물가"), [("", "CPI y/y", "CPI"), ("근원", "Core CPI y/y", "Core CPI")]),
    (("PPI", "생산자물가"), [("", "PPI m/m", "PPI"), ("근원", "Core PPI m/m", "Core PPI")]),
    (("소매판매",), [("", "Retail Sales m/m", "Retail Sales")]),
    (("고용", "비농업"), [("", "Non-Farm Employment Change", "Nonfarm Payrolls"), ("실업률", "Unemployment Rate", "Unemployment Rate")]),
    (("금리결정", "금리 결정"), [("", "Federal Funds Rate", "Fed Interest Rate Decision")]),
    (("GDP",), [("", "Advance GDP q/q", "GDP")]),
)
TICKERS = {
    "마이크론": "MU", "TSMC": "TSM", "테슬라": "TSLA", "알파벳": "GOOGL", "구글": "GOOGL", "마이크로소프트": "MSFT",
    "메타": "META", "애플": "AAPL", "아마존": "AMZN", "엔비디아": "NVDA", "넷플릭스": "NFLX", "브로드컴": "AVGO",
    "AMD": "AMD", "인텔": "INTC", "퀄컴": "QCOM", "오라클": "ORCL", "ASML": "ASML", "팔란티어": "PLTR",
    "도쿄일렉트론": "TOELY", "어도비": "ADBE", "세일즈포스": "CRM", "코스트코": "COST", "나이키": "NKE",
}
_cache: dict[str, object] = {}


def _get(url: str):
    if url not in _cache:
        try:
            req = urllib.request.Request(url, headers=UA)
            _cache[url] = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        except Exception as e:  # 막혀도 리포트는 그대로 나간다
            print("캘린더 수치 조회 실패:", url, type(e).__name__, e)
            _cache[url] = None
    return _cache[url]


def _clean(v) -> str:
    v = (v or "").replace("&nbsp;", "").strip()
    return v


def _ff_events() -> list[dict]:
    out = []
    for u in FF_URLS:
        out += [e for e in (_get(u) or []) if e.get("country") == "USD"]
    return out


def _macro(d: date, text: str) -> str:
    if not text.startswith("미") and "FOMC" not in text:
        return ""
    groups = [(keys[0], s) for keys, s in MACRO if any(k in text for k in keys)]
    if not groups:
        return ""
    # 'PPI·소매판매'처럼 지표가 여럿이면 각 지표 이름을 앞에 붙인다
    series = [((name + " " + label).strip() if len(groups) > 1 and not label else label, ff_t, nq_n)
              for name, s in groups for label, ff_t, nq_n in s]
    days = {d.isoformat(), (d - timedelta(days=1)).isoformat()}  # 캘린더가 한국 날짜로 적힌 경우도 맞춘다
    ff = [e for e in _ff_events() if (e.get("date") or "")[:10] in days]
    parts = []
    for label, ff_title, nq_name in series:
        e = next((x for x in ff if x.get("title") == ff_title), None)
        if not e:
            continue
        fc, prev = _clean(e.get("forecast")), _clean(e.get("previous"))
        actual = ""
        rows = ((_get(NQ_ECON.format(e["date"][:10])) or {}).get("data") or {}).get("rows") or []
        for r in rows:
            if r.get("country") == "United States" and _clean(r.get("eventName")) == nq_name \
                    and _clean(r.get("previous")) == prev and (not fc or _clean(r.get("consensus")) in (fc, "")):
                actual = _clean(r.get("actual"))
                break
        if actual:
            s = f"발표 {actual}" + (f", 예상 {fc}" if fc else "") + (f", 이전 {prev}" if prev else "")
        elif fc or prev:
            s = ", ".join(x for x in (f"예상 {fc}" if fc else "", f"이전 {prev}" if prev else "") if x)
        else:
            continue
        parts.append(f"{label} {s}".strip())
    return " / ".join(parts)


def _earnings(d: date, text: str) -> str:
    name = re.sub(r"\(.*?\)", "", text).strip()
    sym = TICKERS.get(name)
    if not sym:
        return ""
    for day in (d, d + timedelta(days=1), d - timedelta(days=1)):
        rows = ((_get(NQ_EARN.format(day.isoformat())) or {}).get("data") or {}).get("rows") or []
        r = next((x for x in rows if x.get("symbol") == sym), None)
        if not r:
            continue
        fc, eps, sp = _clean(r.get("epsForecast")), _clean(r.get("eps")), _clean(r.get("surprise"))
        if eps:
            s = f"EPS {eps}" + (f", 예상 {fc}" if fc else "")
            try:
                s += f", {float(sp):+.1f}%"
            except ValueError:
                pass
            return s
        return f"EPS 예상 {fc}" if fc else ""
    return ""


def annotate(d: date, field: str, item: str) -> str:
    """★ 항목이면 수치를 붙여 돌려준다: '★미 9월 소비자물가(CPI) (예상 3.6%, 이전 3.4% / 근원 예상 2.5%, 이전 2.4%)'."""
    if not item.startswith("★"):
        return item
    text = item[1:]
    try:
        if field == "macro":
            extra = _macro(d, text)
        elif field == "earnings_us":
            extra = _earnings(d, text)
        else:
            extra = ""
    except Exception as e:
        print("캘린더 수치 붙이기 실패:", item, type(e).__name__, e)
        extra = ""
    return f"{item} ({extra})" if extra else item
