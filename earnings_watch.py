"""실적 속보 자동 감시 (늘 떠 있는 실시간 수급 작업 live_snapshot.py 가 10분마다 실행. earnings-watch.yml 은 수동 실행용).

대상: 증시 캘린더(calendar/YYYY-MM.json)의 어제·오늘 실적 중 ★ 핵심 표시 종목 + 주요 반도체 회사.
감지:
- 국내: 네이버 공시 목록에 그날 이후 '실적' 공시가 올라오면
- 해외: 야후 파이낸스 뉴스에 그날 이후 실적 발표 기사(reports/beats/misses/raises…)가 나오면
감지되면 텔레그램(TELEGRAM_CHAT_ID, 내 채팅)으로 한 번 알리고, out/earnings.json 을 만들어
워크플로가 earnings-live 브랜치에 올린다 → 홈페이지가 실시간으로 읽어 '실적 속보' 카드로 보여준다.
수치(예상치)는 컨센서스(나스닥 EPS·네이버 매출/영업이익)와 기사 제목으로 보여주고, 상세 분석은 하지 않는다.

  python earnings_watch.py            # 감지 → out/earnings.json, out/messages.json, out/report_queue.json(요약 보고서 대상)
  python earnings_watch.py --send     # out/messages.json 텔레그램 발송 (브랜치 푸시 성공 뒤)
  python earnings_watch.py --dry 2026-10-14T15:20   # 그 시각 기준으로 감지만 (발송·저장 없음)
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import kr_surge
import market_calendar as mc

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
SITE = "https://sunbong-oh.github.io/market-dashboard/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
      "Referer": "https://m.stock.naver.com/"}

# 캘린더 이름 → (영문 검색어, 나스닥 심볼). 반도체는 ★가 없어도 감시한다.
FOREIGN = {
    "ASML": ("ASML", "ASML"), "TSMC": ("TSMC", "TSM"), "마이크론": ("Micron", "MU"), "엔비디아": ("Nvidia", "NVDA"),
    "브로드컴": ("Broadcom", "AVGO"), "AMD": ("AMD", "AMD"), "인텔": ("Intel", "INTC"), "퀄컴": ("Qualcomm", "QCOM"),
    "램리서치": ("Lam Research", "LRCX"), "어플라이드머티리얼즈": ("Applied Materials", "AMAT"), "KLA": ("KLA", "KLAC"),
    "텍사스인스트루먼트": ("Texas Instruments", "TXN"), "ARM": ("Arm Holdings", "ARM"), "도쿄일렉트론": ("Tokyo Electron", None),
    "샌디스크": ("Sandisk", "SNDK"), "웨스턴디지털": ("Western Digital", "WDC"), "슈퍼마이크로": ("Super Micro", "SMCI"),
    "테슬라": ("Tesla", "TSLA"), "알파벳": ("Alphabet", "GOOGL"), "구글": ("Alphabet", "GOOGL"), "마이크로소프트": ("Microsoft", "MSFT"),
    "메타": ("Meta", "META"), "애플": ("Apple", "AAPL"), "아마존": ("Amazon", "AMZN"), "넷플릭스": ("Netflix", "NFLX"),
    "오라클": ("Oracle", "ORCL"), "팔란티어": ("Palantir", "PLTR"),
}
# 미국 외 본국 상장 (야후 심볼, 시장 이름) — 실적 후 주가 반응을 함께 본다
HOME = {"ASML": ("ASML.AS", "암스테르담"), "TSMC": ("2330.TW", "대만"), "도쿄일렉트론": ("8035.T", "도쿄")}
DOMESTIC = {"삼성전자": "005930", "SK하이닉스": "000660", "한미반도체": "042700", "삼성전기": "009150", "LG에너지솔루션": "373220",
            "삼성SDI": "006400", "현대차": "005380", "기아": "000270", "NAVER": "035420", "카카오": "035720", "LG전자": "066570",
            "삼성바이오로직스": "207940", "셀트리온": "068270", "POSCO홀딩스": "005490", "DB하이텍": "000990", "리노공업": "058470"}
SEMI = {"ASML", "TSMC", "마이크론", "엔비디아", "브로드컴", "AMD", "인텔", "퀄컴", "램리서치", "어플라이드머티리얼즈", "KLA",
        "텍사스인스트루먼트", "ARM", "도쿄일렉트론", "샌디스크", "웨스턴디지털", "삼성전자", "SK하이닉스", "한미반도체", "DB하이텍", "리노공업"}

PREVIEW = re.compile(r"preview|expect|ahead of|what to watch|to report|will report|set to|due to|options|implied|week ahead|"
                     r"this week|should you|how to play|before (?:its |the )?earnings|on deck|look for|watch for|could|calendar|"
                     r"chatter|rumou?r|\?", re.I)
# 실적 기사로 보려면 실적 단어 + 결과 동사가 함께 있어야 한다 ('ASML Raises Sales Forecast', 'ASML reports €11.7 billion net sales')
EARN_WORD = re.compile(r"\b(?:Q[1-4]|quarter(?:ly)?|earnings|results|revenue|sales|profit|EPS|net income|outlook|forecast|guidance)\b", re.I)
RESULT = re.compile(r"\b(?:reports?|reported|posts?|posted|beats?|miss(?:es|ed)?|tops?|topped|raises?|raised|lifts?|lifted|"
                    r"cuts?|lowers?|lowered|surpass\w*|exceed\w*|falls? short|boosts?|hikes?|trims?|slashes)\b", re.I)
NOW = datetime.now(KST)  # run()에서 기준 시각으로 바꾼다 (--dry 재현용: 그 뒤 기사는 안 본다)
KO_PREVIEW = re.compile(r"전망|예상|앞두고|기대|추정|컨센|프리뷰|관전|주목")


def get(url: str, raw: bool = False):
    try:
        b = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read().decode("utf-8", "replace")
        return b if raw else json.loads(b)
    except Exception as e:
        print("  조회 실패:", url[:90], type(e).__name__, e, flush=True)
        return None


def name_of(item: str) -> str:
    """'★테슬라(키움 28일)' → '테슬라', '★삼성전자 3Q 잠정실적(…)' → '삼성전자'."""
    return re.split(r"[\s(]", item.lstrip("★").strip(), maxsplit=1)[0]


def targets(now: datetime) -> list[dict]:
    """어제·오늘 캘린더 실적 중 감시 대상. 해외(미국 장후)는 한국 다음 날 새벽에 나오므로 어제 것도 본다."""
    out, today = [], now.date()
    for d in (today - timedelta(days=1), today):
        day = (mc._month(d).get("days") or {}).get(d.isoformat()) or {}
        for field in ("earnings_kr", "earnings_us"):
            for item in day.get(field) or []:
                nm = name_of(item)
                if not (item.startswith("★") or nm in SEMI):
                    continue
                if nm in DOMESTIC or (field == "earnings_kr" and nm not in FOREIGN):
                    if nm not in DOMESTIC:
                        print("  종목코드 모름:", nm)
                        continue
                    out.append({"key": f"{d:%Y%m%d}-{nm}", "day": d.isoformat(), "name": nm, "item": item, "kr": True, "code": DOMESTIC[nm]})
                elif nm in FOREIGN:
                    q, sym = FOREIGN[nm]
                    out.append({"key": f"{d:%Y%m%d}-{nm}", "day": d.isoformat(), "name": nm, "item": item, "kr": False, "q": q, "sym": sym})
                else:
                    print("  영문 이름 모름:", nm)
    # 같은 회사가 이틀 연속 적혀 있으면(예: 확정실적) 둘 다 따로 본다. 감시 기간: 그날 0시 ~ 다음 날 18시
    return [t for t in out if now < datetime.fromisoformat(t["day"]).replace(tzinfo=KST) + timedelta(days=1, hours=18)]


def since(t: dict) -> datetime:
    return datetime.fromisoformat(t["day"]).replace(tzinfo=KST)


def google_ko(query: str, after: datetime, n: int = 3) -> list[dict]:
    b = get("https://news.google.com/rss/search?q=" + urllib.parse.quote(query + " when:2d") + "&hl=ko&gl=KR&ceid=KR:ko", raw=True)
    out = []
    try:
        for it in ET.fromstring(b or "<rss/>").iter("item"):
            title, src = it.findtext("title") or "", it.findtext("source") or ""
            dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
            title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
            if after <= dt <= NOW and not KO_PREVIEW.search(title):
                out.append({"t": title, "u": it.findtext("link") or "", "o": src, "dt": dt.strftime("%m/%d %H:%M")})
    except ET.ParseError:
        pass
    out.sort(key=lambda a: a["dt"], reverse=True)
    return out[:n]


def detect_foreign(t: dict) -> dict | None:
    after = since(t)
    key = t["q"].split()[0].lower()
    hits, seen = [], set()
    news = []
    for q in (t["sym"] or t["q"], t["q"] + " earnings"):  # 심볼 검색만으로는 보도자료가 빠질 때가 있다
        j = get("https://query2.finance.yahoo.com/v1/finance/search?q=" + urllib.parse.quote(q) + "&newsCount=20&quotesCount=0") or {}
        news += [n for n in j.get("news") or [] if n.get("uuid") not in seen and not seen.add(n.get("uuid"))]
    for n in news:
        title = n.get("title") or ""
        dt = datetime.fromtimestamp(n.get("providerPublishTime") or 0, KST)
        if not after <= dt <= NOW or key not in title.lower() or PREVIEW.search(title) or not (RESULT.search(title) and EARN_WORD.search(title)):
            continue
        hits.append({"t": title, "u": n.get("link") or "", "o": n.get("publisher") or "", "dt": dt.strftime("%m/%d %H:%M")})
    ko = google_ko(f"{t['name']} 실적", after, 6)
    # 한국 기사로도 감지: 회사명 + 분기/실적 + 매출·영업이익·가이던스 + 숫자
    ko_hits = [a for a in ko if t["name"].lower() in a["t"].lower()
               and re.search(r"(?:분기|실적).{0,25}(?:매출|영업이익|순이익|EPS|가이던스|전망치)", a["t"]) and re.search(r"\d", a["t"])]
    if not hits and len(ko_hits) < 2:
        return None
    hits.sort(key=lambda a: a["dt"])
    cons = []
    if t["sym"]:
        for d in (t["day"], (date.fromisoformat(t["day"]) + timedelta(days=1)).isoformat()):
            rows = ((get(f"https://api.nasdaq.com/api/calendar/earnings?date={d}") or {}).get("data") or {}).get("rows") or []
            r = next((x for x in rows if x.get("symbol") == t["sym"]), None)
            if r:
                if r.get("epsForecast"):
                    cons.append(f"EPS 컨센서스 {r['epsForecast']} ({r.get('noOfEsts') or '?'}명, 나스닥)")
                if r.get("eps"):
                    cons.append(f"EPS 발표 {r['eps']}" + (f" (서프라이즈 {float(r['surprise']):+.1f}%)" if r.get("surprise") else ""))
                break
    first = min([a["dt"] for a in hits] + [a["dt"] for a in ko_hits])
    return {"first": first, "en": hits[-3:][::-1], "ko": ko[:3], "cons": cons}


def naver_consensus(code: str) -> list[str]:
    j = get(f"https://m.stock.naver.com/api/stock/{code}/finance/quarter") or {}
    info = j.get("financeInfo") or {}
    col = next((c for c in info.get("trTitleList") or [] if c.get("isConsensus") == "Y"), None)
    if not col:
        return []
    out = []
    for row in info.get("rowList") or []:
        if row.get("title") in ("매출액", "영업이익"):
            v = ((row.get("columns") or {}).get(col["key"]) or {}).get("value") or ""
            try:
                eok = float(v.replace(",", ""))
                out.append(f"{row['title']} {eok / 10000:,.1f}조" if eok >= 10000 else f"{row['title']} {eok:,.0f}억")
            except ValueError:
                pass
    return [f"{col['title'].rstrip('.')} 컨센서스(네이버): " + " · ".join(out)] if out else []


def detect_domestic(t: dict) -> dict | None:
    after = since(t)
    rows = get(f"https://m.stock.naver.com/api/stock/{t['code']}/disclosure?pageSize=10&page=1") or []
    hits = [r for r in rows if "실적" in (r.get("title") or "")
            and after <= datetime.fromisoformat(r["datetime"]).replace(tzinfo=KST) <= NOW]
    disc = None
    if hits:
        r = hits[-1]
        disc = {"t": r["title"], "u": f"https://m.stock.naver.com/domestic/stock/{t['code']}/disclosure",
                "o": "공시", "dt": datetime.fromisoformat(r["datetime"]).strftime("%m/%d %H:%M")}
    news = []
    for blk in get(f"https://m.stock.naver.com/api/news/stock/{t['code']}?pageSize=20&page=1") or []:
        for a in blk.get("items") or []:
            dt = datetime.strptime(a["datetime"], "%Y%m%d%H%M").replace(tzinfo=KST)
            title = html.unescape(a.get("titleFull") or a.get("title") or "")
            if after <= dt <= NOW and re.search(r"실적|영업이익|영업익|매출", title) and not KO_PREVIEW.search(title):
                news.append({"t": title, "u": a.get("mobileNewsUrl") or "", "o": a.get("officeName") or "", "dt": dt.strftime("%m/%d %H:%M")})
    news.sort(key=lambda a: a["dt"], reverse=True)
    # 확정실적처럼 공시 제목에 '실적'이 없을 때: 그날 실적 발표 기사가 2건 이상이면 발표로 본다
    done = [a for a in news if re.search(r"(?:영업이익|영업익|매출|순이익).{0,12}\d[\d.,]*\s*(?:조|억)", a["t"])]
    if not disc and len(done) < 2:
        return None
    first = disc["dt"] if disc else min(a["dt"] for a in done)
    return {"first": first, "en": [disc] if disc else [], "ko": news[:3], "cons": naver_consensus(t["code"])}


LATE_DETECT = timedelta(hours=3)  # 발표 후 이보다 늦게 감지되면 텔레그램은 보내지 않는다
LATE_REACT = timedelta(hours=2)   # 첫 정규장 마감 후 이보다 늦게 확인되면 주가 반응 알림은 보내지 않는다
CUR = {"USD": "$", "EUR": "€", "TWD": "NT$", "JPY": "¥", "KRW": ""}


def fmt_px(v: float, cur: str | None) -> str:
    sign = CUR.get(cur or "", "")
    return f"{sign}{v:,.0f}" + ("원" if cur == "KRW" else "") if v >= 1000 and cur in ("KRW", "JPY", "TWD") else f"{sign}{v:,.2f}"


def yahoo_reaction(sym: str, label: str, since: datetime) -> dict | None:
    """야후 차트(5일·5분, 장전·시간외 포함)로 '발표 전 종가 대비' 등락과 지금 세션, 발표 후 첫 정규장 종가 등락."""
    j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=5m&includePrePost=true") or {}
    try:
        r = j["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None
    closes = r["indicators"]["quote"][0].get("close") or []
    pts = [(t, c) for t, c in zip(r.get("timestamp") or [], closes) if c is not None]
    tp = r["meta"].get("tradingPeriods") or {}
    reg = tp.get("regular") if isinstance(tp, dict) else tp
    periods = sorted((p[0]["start"], p[0]["end"]) for p in reg or [] if p)
    if not pts or not periods:
        return None
    s_ts, n_ts = since.timestamp(), NOW.timestamp()
    pts = [(t, c) for t, c in pts if t <= n_ts]
    if not pts:
        return None

    def close_of(p):
        v = [c for t, c in pts if p[0] <= t <= p[1]]
        return v[-1] if v else None
    if any(p[0] <= s_ts < p[1] for p in periods):  # 정규장 중 발표 → 발표 시점 가격 기준
        v = [c for t, c in pts if t <= s_ts]
        ref, ref_l = (v[-1] if v else None), "발표 시점"
    else:
        done = [p for p in periods if p[1] <= s_ts]
        ref, ref_l = (close_of(done[-1]) if done else None), "발표 전 종가"
    t, price = pts[-1]
    when = datetime.fromtimestamp(t, KST).strftime("%m/%d %H:%M")
    cur = r["meta"].get("currency")
    if t < s_ts:  # 발표 뒤 아직 거래가 없음 (예: ASML 14시 발표 → 미국 장전 거래는 17시부터)
        return {"l": label, "s": "발표 후 거래 전", "t": when, "p": fmt_px(price, cur), "c": None, "ref": ref_l, "d1": None}
    i = max((k for k, p in enumerate(periods) if p[0] <= t), default=None)
    if i is not None and t < periods[i][1]:
        sess = "정규장"
    elif "." in sym:
        sess = "종가"
    elif i is not None and t - periods[i][1] <= 4 * 3600 + 600:
        sess = "애프터마켓"
    else:
        sess = "프리마켓"
    d1p = next((p for p in periods if p[0] >= s_ts and p[1] <= n_ts), None)  # 발표 후 첫 정규장 (끝난 것)
    d1 = close_of(d1p) if d1p else None
    pct = lambda v: round((v / ref - 1) * 100, 2) if ref and v else None
    d1t = datetime.fromtimestamp(d1p[1], KST).isoformat() if d1p else None
    return {"l": label, "s": sess, "t": when, "p": fmt_px(price, cur), "c": pct(price), "ref": ref_l, "d1": pct(d1), "d1t": d1t}


def naver_reaction(code: str, label: str, since: datetime) -> dict | None:
    j = get(f"https://m.stock.naver.com/api/stock/{code}/basic") or {}
    try:
        price, chg = float(j["closePrice"].replace(",", "")), float(j["fluctuationsRatio"])
    except (KeyError, ValueError, AttributeError):
        return None
    open_ = j.get("marketStatus") == "OPEN"
    # 장 마감(15:30) 뒤 발표면 오늘 등락은 발표 전 → 다음 거래일부터 반응
    d1_day = since.date() if since.strftime("%H%M") < "1530" else since.date() + timedelta(days=1)
    while d1_day.weekday() >= 5:
        d1_day += timedelta(days=1)
    if NOW.date() < d1_day:
        return {"l": label, "s": "발표 후 정규장 전", "t": NOW.strftime("%m/%d %H:%M"), "p": f"{price:,.0f}원", "c": None, "ref": "전일 종가", "d1": None}
    d1 = chg if NOW.date() == d1_day and not open_ and NOW.strftime("%H%M") >= "1535" else None
    d1t = datetime.combine(d1_day, datetime.min.time()).replace(hour=15, minute=30, tzinfo=KST).isoformat() if d1 is not None else None
    return {"l": label, "s": "장중" if open_ else "종가", "t": NOW.strftime("%m/%d %H:%M"), "p": f"{price:,.0f}원", "c": chg, "ref": "전일 종가",
            "d1": d1, "d1t": d1t}


def reactions(it: dict) -> list[dict]:
    since = datetime.fromisoformat(it["first_iso"])
    if it.get("kr"):
        r = naver_reaction(it["code"], it["name"], since)
        return [r] if r else []
    out = []
    if it.get("sym"):
        out.append(yahoo_reaction(it["sym"], f"{it['name']}(미국 {it['sym']})", since))
    if it["name"] in HOME:
        hs, market = HOME[it["name"]]
        out.append(yahoo_reaction(hs, f"{it['name']}({market})", since))
    return [r for r in out if r]


def px_line(r: dict) -> str:
    s = f"{r['l']} {r['s']}({r.get('t', '')}) {r['p']}"
    if r.get("c") is not None:
        s += f" ({r['ref']} 대비 {r['c']:+.2f}%)"
    if r.get("d1") is not None:
        s += f" · 발표 후 첫 정규장 {r['d1']:+.2f}%"
    return s


def message(t: dict, res: dict) -> str:
    lines = [f"📊 실적 발표 · {t['name']}", f"(캘린더: {t['item'].lstrip('★')} · {t['day'][5:].replace('-', '/')})", ""]
    lines += [f"▶ {c}" for c in res["cons"]]
    lines += [f"📈 {px_line(r)}" for r in res.get("px") or []]
    if res["cons"] or res.get("px"):
        lines.append("")
    for a in res["en"] + res["ko"]:
        lines.append(f"• {a['t']} ({a['o']} {a['dt']})")
    lines += ["", f"홈페이지: {SITE}"]
    return "\n".join(lines)


def merge_px(old: list | None, new: list) -> list:
    """새로 받은 주가로 바꾸되, 한 번 잡은 '발표 후 첫 정규장' 등락은 유지한다 (날이 지나면 다시 못 구하므로)."""
    if not new:
        return old or []
    prev = {r["l"]: r for r in old or []}
    for r in new:
        if r.get("d1") is None and prev.get(r["l"], {}).get("d1") is not None:
            r["d1"], r["d1t"] = prev[r["l"]]["d1"], prev[r["l"]].get("d1t")
    return new


# ---------- 국내 영업이익 급증 (DART 잠정실적 공시) ----------
SURGE_FROM, SURGE_TO = "0730", "1900"  # 공시가 올라오는 시간대에만 본다


def surge_scan(now: datetime, seen: set[str]) -> list[dict]:
    """오늘(이른 아침이면 어제까지) 잠정실적 공시 중 전분기 대비 영업이익이 급증한 종목.
    한 번 본 공시는 seen 에 넣어 다시 본문을 받지 않는다 (10분마다 도는 작업이라 공시 수만큼만 읽는다)."""
    if now.weekday() >= 5 or not (SURGE_FROM <= now.strftime("%H%M") <= SURGE_TO):
        return []
    days = [now.strftime("%Y%m%d")]
    if now.strftime("%H%M") < "1000":  # 어제 장 마감 뒤 올라온 공시도 한 번 더 본다
        days.append((now - timedelta(days=1)).strftime("%Y%m%d"))
    out = []
    for day in days:
        try:
            rows = [r for r in kr_surge.list_day(day) if r["rcp"] not in seen]
        except Exception as e:
            print("  공시 목록 실패:", day, type(e).__name__, e, flush=True)
            continue
        for r in rows:
            seen.add(r["rcp"])
            try:
                s = kr_surge.check(r, day)
            except Exception as e:
                print("  공시 본문 실패:", r["corp"], type(e).__name__, e, flush=True)
                continue
            if s:
                out.append(s)
                print("  영업이익 급증:", s["name"], s["code"], " / ".join(kr_surge.summary(s)), flush=True)
    return out


def surge_message(it: dict) -> str:
    lines = [f"🚀 국내 실적 급증 · {it['name']}({it['code']})", it["item"], ""]
    lines += [f"▶ {c}" for c in it["cons"]]
    lines += [f"📈 {px_line(r)}" for r in it.get("px") or []]
    lines += [""] + [f"• {a['t']} ({a['o']} {a['dt']})" for a in it["news"]]
    return "\n".join(lines + ["", f"홈페이지: {SITE}"])


def previous() -> dict:
    r = subprocess.run(["git", "show", "origin/earnings-live:earnings.json"], cwd=ROOT, capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8")) if r.returncode == 0 else {}
    except ValueError:
        return {}


def run(now: datetime, dry: bool = False) -> None:
    global NOW
    NOW = now
    old = {} if dry else previous()
    items = {i["key"]: i for i in old.get("items") or []
             if now - datetime.fromisoformat(i["day"]).replace(tzinfo=KST) < timedelta(days=3)}
    msgs = []
    for t in targets(now):
        print("감시:", t["key"], flush=True)
        res = detect_domestic(t) if t["kr"] else detect_foreign(t)
        if not res:
            continue
        prev = items.get(t["key"]) or {}
        first_iso = prev.get("first_iso") or datetime.strptime(f"{t['day'][:4]}/{res['first']}", "%Y/%m/%d %H:%M").replace(tzinfo=KST).isoformat()
        it = dict(prev)
        it.update({"key": t["key"], "day": t["day"], "name": t["name"], "item": t["item"].lstrip("★"),
                   "detected": prev.get("detected") or now.strftime("%Y-%m-%d %H:%M"), "first": prev.get("first") or res["first"], "first_iso": first_iso,
                   "kr": t["kr"], "code": t.get("code"), "sym": t.get("sym"),
                   "cons": res["cons"], "news": (res["en"] + res["ko"])[:6], "sent": True})
        it["px"] = merge_px(prev.get("px"), reactions(it))
        items[t["key"]] = it
        if not prev.get("sent"):
            if now - datetime.fromisoformat(first_iso) <= LATE_DETECT:
                res["px"] = it["px"]
                msgs.append(message(t, res))
            else:  # 발표 뒤 한참 지나서야 감지됨 → 늦은 알림은 보내지 않는다 (홈페이지만 갱신)
                print("  늦게 감지 → 텔레그램 생략", first_iso, flush=True)
        print("  감지:", res["first"], [a["t"][:50] for a in res["en"]], it["px"], flush=True)
    # 국내 잠정실적 공시 중 전분기 대비 영업이익 급증 종목 (캘린더에 없어도 찾아낸다)
    scanned = set(old.get("kr_seen") or [])
    queue = []  # 카드가 나간 종목 → 요약 보고서 작업(surge-report.yml)으로 넘긴다
    for s in surge_scan(now, scanned):
        key = f"KR-{s['rcp']}"
        prev = items.get(key) or {}
        at = s.get("at") or now.isoformat(timespec="minutes")
        first_iso = prev.get("first_iso") or datetime.fromisoformat(at).replace(tzinfo=KST).isoformat()
        day = first_iso[:10]
        it = dict(prev)
        it.update({"key": key, "day": day, "name": s["name"], "code": s["code"], "kr": True, "surge": True,
                   "icon": "🚀", "head": "영업이익 급증", "note": "공시", "item": f"{s['period']} 잠정실적",
                   "detected": prev.get("detected") or now.strftime("%Y-%m-%d %H:%M"),
                   "first": prev.get("first") or datetime.fromisoformat(at).strftime("%m/%d %H:%M"), "first_iso": first_iso,
                   "cons": kr_surge.summary(s),
                   "news": [{"t": s["title"], "u": s["url"], "o": "공시", "dt": datetime.fromisoformat(at).strftime("%m/%d %H:%M")}],
                   "sent": True})
        it["px"] = merge_px(prev.get("px"), reactions(it))
        items[key] = it
        if not prev.get("sent"):
            if now - datetime.fromisoformat(first_iso) <= LATE_DETECT:
                msgs.append(surge_message(it))
                queue.append(f"{s['code']}:{s['rcp']}")
            else:
                print("  늦게 감지 → 텔레그램 생략", key, first_iso, flush=True)
    # 감시 기간이 끝난 항목도 사흘 동안 주가 반응을 갱신하고, 발표 후 첫 정규장이 끝나면 한 번 알린다
    seen = {t["key"] for t in targets(now)}
    for it in items.values():
        if it["key"] not in seen and it.get("first_iso"):
            it["px"] = merge_px(it.get("px"), reactions(it))
        main = (it.get("px") or [None])[0]
        if main and main.get("d1") is not None and not it.get("react_sent"):
            it["react_sent"] = True
            d1t = main.get("d1t")
            if not d1t or now - datetime.fromisoformat(d1t) > LATE_REACT:  # 마감 뒤 한참 지남 → 늦은 알림 생략
                print("  주가 반응 늦게 확인 → 텔레그램 생략", it["key"], d1t, flush=True)
                continue
            msgs.append("\n".join([f"📈 실적 후 주가 반응 · {it['name']}", ""] + [px_line(r) for r in it["px"]] + ["", f"홈페이지: {SITE}"]))
    # 이미 읽어 본 공시는 적어 두고(최근 것만) 다음 실행 때 본문을 다시 받지 않는다
    data = {"updated": now.strftime("%Y-%m-%d %H:%M"), "items": sorted(items.values(), key=lambda i: i["detected"], reverse=True),
            "kr_seen": sorted(scanned)[-800:]}
    if dry:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        print("\n---\n".join(msgs))
        return
    OUT.mkdir(exist_ok=True)
    # 내용이 바뀌었을 때만 올린다 (updated 시각만 바뀐 건 무시)
    changed = data["items"] != (old.get("items") or []) or data["kr_seen"] != (old.get("kr_seen") or [])
    if changed:
        (OUT / "earnings.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (OUT / "messages.json").write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
    (OUT / "report_queue.json").write_text(json.dumps(queue), encoding="utf-8")
    print("변경:", changed, "새 알림:", len(msgs))


def send() -> None:
    msgs = json.loads((OUT / "messages.json").read_text(encoding="utf-8")) if (OUT / "messages.json").exists() else []
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    for m in msgs:
        data = urllib.parse.urlencode({"chat_id": chat, "text": m, "disable_web_page_preview": "true"}).encode()
        r = urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=30)
        print("텔레그램:", r.status)


if __name__ == "__main__":
    if "--send" in sys.argv:
        send()
    elif "--dry" in sys.argv:
        run(datetime.fromisoformat(sys.argv[sys.argv.index("--dry") + 1]).replace(tzinfo=KST), dry=True)
    else:
        run(datetime.now(KST))
