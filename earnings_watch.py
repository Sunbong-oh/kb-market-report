"""실적 속보 자동 감시 (earnings-watch.yml이 10분마다 실행).

대상: 증시 캘린더(calendar/YYYY-MM.json)의 어제·오늘 실적 중 ★ 핵심 표시 종목 + 주요 반도체 회사.
감지:
- 국내: 네이버 공시 목록에 그날 이후 '실적' 공시가 올라오면
- 해외: 야후 파이낸스 뉴스에 그날 이후 실적 발표 기사(reports/beats/misses/raises…)가 나오면
감지되면 텔레그램(TELEGRAM_CHAT_ID, 내 채팅)으로 한 번 알리고, out/earnings.json 을 만들어
워크플로가 earnings-live 브랜치에 올린다 → 홈페이지가 실시간으로 읽어 '실적 속보' 카드로 보여준다.
수치(예상치)는 컨센서스(나스닥 EPS·네이버 매출/영업이익)와 기사 제목으로 보여주고, 상세 분석은 하지 않는다.

  python earnings_watch.py            # 감지 → out/earnings.json, out/messages.json
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
            if dt >= after and not KO_PREVIEW.search(title):
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
        if dt < after or key not in title.lower() or PREVIEW.search(title) or not (RESULT.search(title) and EARN_WORD.search(title)):
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
            and datetime.fromisoformat(r["datetime"]).replace(tzinfo=KST) >= after]
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
            if dt >= after and re.search(r"실적|영업이익|영업익|매출", title) and not KO_PREVIEW.search(title):
                news.append({"t": title, "u": a.get("mobileNewsUrl") or "", "o": a.get("officeName") or "", "dt": dt.strftime("%m/%d %H:%M")})
    news.sort(key=lambda a: a["dt"], reverse=True)
    # 확정실적처럼 공시 제목에 '실적'이 없을 때: 그날 실적 발표 기사가 2건 이상이면 발표로 본다
    done = [a for a in news if re.search(r"(?:영업이익|영업익|매출|순이익).{0,12}\d[\d.,]*\s*(?:조|억)", a["t"])]
    if not disc and len(done) < 2:
        return None
    first = disc["dt"] if disc else min(a["dt"] for a in done)
    return {"first": first, "en": [disc] if disc else [], "ko": news[:3], "cons": naver_consensus(t["code"])}


def message(t: dict, res: dict) -> str:
    lines = [f"📊 실적 발표 · {t['name']}", f"(캘린더: {t['item'].lstrip('★')} · {t['day'][5:].replace('-', '/')})", ""]
    lines += [f"▶ {c}" for c in res["cons"]]
    if res["cons"]:
        lines.append("")
    for a in res["en"] + res["ko"]:
        lines.append(f"• {a['t']} ({a['o']} {a['dt']})")
    lines += ["", f"홈페이지: {SITE}"]
    return "\n".join(lines)


def previous() -> dict:
    r = subprocess.run(["git", "show", "origin/earnings-live:earnings.json"], cwd=ROOT, capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8")) if r.returncode == 0 else {}
    except ValueError:
        return {}


def run(now: datetime, dry: bool = False) -> None:
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
        items[t["key"]] = {"key": t["key"], "day": t["day"], "name": t["name"], "item": t["item"].lstrip("★"),
                           "detected": prev.get("detected") or now.strftime("%Y-%m-%d %H:%M"), "first": res["first"],
                           "cons": res["cons"], "news": (res["en"] + res["ko"])[:6], "sent": True}
        if not prev.get("sent"):
            msgs.append(message(t, res))
        print("  감지:", res["first"], [a["t"][:50] for a in res["en"]], flush=True)
    data = {"updated": now.strftime("%Y-%m-%d %H:%M"), "items": sorted(items.values(), key=lambda i: i["detected"], reverse=True)}
    if dry:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        print("\n---\n".join(msgs))
        return
    OUT.mkdir(exist_ok=True)
    # 내용이 바뀌었을 때만 올린다 (updated 시각만 바뀐 건 무시)
    changed = [i for i in data["items"]] != (old.get("items") or [])
    if changed:
        (OUT / "earnings.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (OUT / "messages.json").write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")
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
