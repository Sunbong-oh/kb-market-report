"""퇴근길 음성 브리핑: 장마감 리포트(report.py가 남긴 reports/latest.html 스냅샷)를 라디오 원고로 바꿔
한국어 음성(MP3)으로 만들고, 팟캐스트 피드(feed.xml)를 갱신한다. (market-dashboard의 출근길 briefing.py와 같은 방식)

실행: python close_briefing.py [--pod DIR] [--notify] [--text-only] [--html 스냅샷.html]
  --pod DIR    팟캐스트 폴더(podcast 브랜치 체크아웃). 오늘 회차 MP3를 넣고 feed.xml을 다시 쓴다.
  --notify     텔레그램으로도 MP3를 보낸다(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID).
  --text-only  원고만 만든다(음성 생성 안 함).
  --html PATH  읽을 스냅샷(기본 reports/latest.html). 예: --html "latest/장마감 수급체크.html"
원고는 reports/close_briefing.txt, 음성은 reports/close_briefing.mp3.

순서: 시장 지수 등락률 -> 투자자별 수급 -> 오늘 상승·하락 요인(마감 시황 기사·핵심 이슈) -> 강세 테마
-> 목표주가 상향 리포트(종목·제목·현재가 대비 상승여력) -> 오늘 저녁 미국 주요 일정·실적 발표.
음성: ElevenLabs 키가 있으면 내 목소리(음성 복제), 없으면 Microsoft Edge 온라인 TTS(edge-tts, 키 불필요).
차에서는 팟캐스트 앱(AntennaPod 등)에 FEED_URL을 구독해 두면 안드로이드 오토에서 바로 재생된다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from html import escape
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")
KST = timezone(timedelta(hours=9))
SNAPSHOT = ROOT / "reports" / "latest.html"
TXT = ROOT / "reports" / "close_briefing.txt"
MP3 = ROOT / "reports" / "close_briefing.mp3"

VOICE = "ko-KR-HyunsuMultilingualNeural"  # 출근길 브리핑과 같은 목소리
RATE = "+15%"
PITCH = "-8Hz"
# GitHub Pages 없이 podcast 브랜치 파일을 그대로 주소로 쓴다(팟캐스트 앱은 형식을 enclosure type으로 판단)
POD_BASE = "https://raw.githubusercontent.com/Sunbong-oh/kb-market-report/podcast"
FEED_URL = f"{POD_BASE}/feed.xml"
KEEP = 14  # 피드에 남길 회차 수(오래된 MP3는 지운다)
SHOW_TITLE = "퇴근길 국내 증시 장마감 브리핑"

HOST = "주식쟁이 봉"  # 붙여 쓰면 '주식쟁 이봉'으로 끊어 읽는다
PAUSE = "⏸"  # 원고 속 쉼표 하나 = 무음 0.5초. 항목 제목 뒤 1번, 항목 사이 2번
SOUND = (("신고가", "신고까"), ("주가", "주까"))  # 소리 나는 대로 읽힐 낱말(원고 파일에는 원래 표기)
THEMES_READ = 5
NAMES = {"BoA": "뱅크오브아메리카", "JP모간": "제이피모건", "GS": "골드만삭스", "MS": "모건스탠리", "J&J": "존슨앤존슨"}


# ---------- 읽기 좋은 문장으로 ----------
def speakable(t: str) -> str:
    """기사 제목·캘린더 항목의 한자·기호를 소리 내어 읽기 좋게 바꾼다."""
    t = re.sub(r"\s*\[[^\]]*\]\s*", " ", t)  # [마감시황] 같은 머리표
    t = re.sub(r"^\s*(속보|종합|1보|2보)\s*[:)\]]?\s*", "", t)
    t = re.sub(r"\(([一-鿿]+)\)", "", t)
    t = re.sub(r"([가-힣])\s*\([A-Z]{2,6}\)", r"\1", t)  # 소비자물가(CPI) -> 소비자물가
    t = re.sub(r"\(~(\d+일)\)", r", \1까지", t)
    t = re.sub(r"(\d)~(\d)", r"\1에서 \2", t)
    t = re.sub(r"\$(\d[\d.,]*)", r"\1달러", t)
    t = re.sub(r"\.{2,}", ", ", t)
    t = t.replace(" / ", ", ").replace("EPS", "주당순이익")
    t = re.sub(r"(^|\s)미 ", r"\1미국 ", t)
    for a, b in (("美", "미국 "), ("中", "중국 "), ("日", "일본 "), ("韓", "한국 "), ("北", "북한 "), ("英", "영국 "),
                 ("外人", "외국인"), ("外", "외국인 "), ("株", "주"), ("↑", " 상승"), ("↓", " 하락"), ("…", ", "),
                 ("⋯", ", "), ("·", ", "), ("&", " 앤 "), ("%p", "%포인트"), ("vs", "대"), ("→", "에서 "),
                 ("외인", "외국인"), ("기관·외인", "기관과 외국인"), ("삼전닉스", "삼성전자와 SK하이닉스"),
                 ("SK하닉", "SK하이닉스"), ("삼전", "삼성전자"), ("코스피200", "코스피 200")):
        t = t.replace(a, b)
    t = re.sub(r"[\"'“”‘’<>《》「」『』\[\]]", "", t)
    t = re.sub(r"\s*,(\s*,)*", ",", t)
    return re.sub(r"\s+", " ", t).strip(" ,.")


def pct(v: float, d: int = 2) -> str:
    return f"{abs(v):.{d}f}%"


def word(v: float, up="상승", down="하락", flat="보합") -> str:
    return up if v > 0.005 else down if v < -0.005 else flat


def eok(v: float) -> str:
    """억원 금액을 읽는 말로: 11831 -> '1조 1831억 원'. 쉼표 없이(TTS가 자릿수 쉼표를 엉뚱하게 읽음)."""
    v = round(abs(v))
    jo, rest = divmod(v, 10000)
    if jo:
        return f"{jo}조 {rest}억 원" if rest else f"{jo}조 원"
    return f"{rest}억 원"


def josa(w: str, a="은", b="는") -> str:
    c = w.strip()[-1:]
    if "가" <= c <= "힣":
        return a if (ord(c) - 0xAC00) % 28 else b
    return a if c in "013678LMNRlmnr" else b


def gram(t: str) -> set[str]:
    t = re.sub(r"[^가-힣A-Za-z0-9]", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def distinct(titles, seen=(), limit=2, thr=0.4):
    """제목끼리 글자 2-gram이 많이 겹치면(같은 시황 기사의 받아쓰기) 하나만 남긴다."""
    picked, grams = [], [gram(t) for t in seen]
    for t in titles:
        g = gram(t)
        if g and all(len(g & h) / min(len(g), len(h) or 1) < thr for h in grams):
            picked.append(t)
            grams.append(g)
        if len(picked) >= limit:
            break
    return picked


# ---------- 자료 ----------
def load_snapshot(path: Path) -> tuple[dict, datetime]:
    """report.py가 만든 스냅샷 HTML에 내장된 /api 응답과 기준 시각."""
    html = path.read_text(encoding="utf-8")
    m = re.search(r"window\.__SNAPSHOT__ = (.*?);\nwindow\.__SNAPSHOT_TIME__ = (\".*?\");", html, re.S)
    if not m:
        raise RuntimeError(f"스냅샷 자료를 찾지 못했습니다: {path}")
    api = json.loads(m.group(1).replace("<\\/", "</"))
    taken = json.loads(m.group(2))  # '2026-10-14(화) 15:58'
    now = datetime.strptime(re.sub(r"\(.\)", "", taken), "%Y-%m-%d %H:%M")
    return api, now


# 마감 시황 기사 제목에서 원인 구절을 뽑는다: 'ASML 실적 실망에 0.44% 하락' -> 'ASML 실적 실망'
CAUSE = re.compile(r"^(.+?)(?:에|여파로|여파에|탓에|우려에|겹치자|덕에|속에)\s")
NOT_CAUSE = re.compile(r"\d\s*(%|％|포인트|선|p\b|P\b)|대$|\S+[은는]\s|코스피|코스닥|지수|장중|장 초반|마감|^(국내\s*)?증시")


def clean_title(title: str) -> str:
    t = re.sub(r"\s+-\s+[^-]+$", "", title)  # 구글 뉴스 '- 매체' 꼬리
    t = re.sub(r"\s+By\s+.*$", "", t)  # Investing.com 'By 매체'
    t = re.sub(r"\[[^\]]*\]|\((?:종합|\d보|마감시황)\)|\(마감\)", " ", t)
    t = re.sub(r"(\d),(\d{3})", r"\1\2", t)  # 6,008 -> 6008 (쉼표로 끊기지 않게)
    return re.sub(r"\s+", " ", t).strip()


def causes_of(title: str) -> list[str]:
    out = []
    for seg in re.split(r"…|⋯|\.{2,}|·{2,}|,|;|\s-\s", clean_title(title)):
        m = CAUSE.match(seg.strip(" '\"‘’“”") + " ")
        if m:
            c = m.group(1).strip(" '\"‘’“”")
            if not NOT_CAUSE.search(c) and len(re.findall(r"[가-힣A-Za-z]{2,}", c)) >= 1 and len(c) >= 4:
                out.append(c.replace("외인", "외국인").replace("매도세", "매도").replace("매수세", "매수"))
    return out


def news_rows(now: datetime) -> list[dict]:
    """오늘 마감 시황 기사(구글 뉴스 '코스피 마감') + 네이버 증권 주요뉴스. [{'t': 제목, 'dt': 'YYYYmmddHHMMSS', 'src'}]"""
    from market_issues import HEADERS, fetch_news
    rows = []
    with httpx.Client(headers=HEADERS, timeout=10, follow_redirects=True) as http:
        for q in ("코스피 마감", "코스닥 마감"):
            try:
                xml = http.get("https://news.google.com/rss/search",
                               params={"q": f"{q} when:1d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"}).text
                for it in ET.fromstring(xml).iter("item"):
                    try:
                        dt = parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST)
                    except Exception:
                        continue
                    if dt.date() == now.date():
                        rows.append({"t": it.findtext("title") or "", "dt": f"{dt:%Y%m%d%H%M%S}", "src": "recap"})
            except Exception as e:
                print("마감 시황 기사 실패:", q, type(e).__name__, e, file=sys.stderr)
        try:
            for r in fetch_news(http, hours=10):
                if r["dt"].startswith(f"{now:%Y%m%d}"):
                    rows.append({**r, "src": "naver"})
        except Exception as e:
            print("주요뉴스 실패:", type(e).__name__, e, file=sys.stderr)
    return rows


def market_reasons(rows: list[dict], limit: int = 3) -> list[str]:
    """시황 기사들이 꼽은 원인 구절을 비슷한 것끼리 묶어, 여러 매체가 함께 꼽은 순으로(최대 limit개).
    예: '외국인 매도'(9곳) · 'ASML 실적 실망'(3곳) · '반도체주 부진'(2곳)."""
    groups: list[dict] = []
    for r in sorted(rows, key=lambda r: r["dt"], reverse=True):
        if r["src"] == "naver" and not re.search(r"코스피|코스닥|증시|국장", r["t"]):
            continue
        for c in causes_of(r["t"]):
            g = gram(c)
            hit = next((x for x in groups if len(g & x["g"]) / min(len(g), len(x["g"]) or 1) >= 0.5), None) if g else None
            if hit:
                hit["n"] += 1
                hit["g"] |= g
                hit["texts"].append(c)
            else:
                groups.append({"g": set(g), "n": 1, "texts": [c]})
    groups.sort(key=lambda x: -x["n"])  # 안정 정렬: 같은 수면 최근 기사 먼저
    # 묶음의 대표 표현 = 가장 많이 쓰인 표현(같으면 최근 것)
    return [max(x["texts"], key=x["texts"].count) for x in groups[:limit]]


def hot_issues(rows: list[dict], seen: list[str]) -> list[str]:
    """네이버 주요뉴스를 같은 이야기끼리 묶어 기사가 많이 몰린 이슈 제목(라이브 화면의 '오늘의 핵심 이슈'와 같은 규칙)."""
    from market_issues import build
    naver = [r for r in rows if r["src"] == "naver"]
    if not naver:
        return []
    try:
        titles = [g["title"] for g in build(naver, None, top=3)]
    except Exception as e:
        print("핵심 이슈 실패:", type(e).__name__, e, file=sys.stderr)
        return []
    return distinct(titles, seen=seen, limit=2, thr=0.35)


def close_notes(now: datetime) -> str | None:
    """루틴·사람이 남긴 해설 close_notes/YYYY-MM-DD.txt(있을 때만 '자세히 짚어 보겠습니다'로 읽는다)."""
    try:
        return re.sub(r"\s+", " ", (ROOT / "close_notes" / f"{now:%Y-%m-%d}.txt").read_text(encoding="utf-8")).strip() or None
    except OSError:
        return None


def tonight_us(now: datetime) -> dict:
    """증시 캘린더(calendar/YYYY-MM.json)의 오늘 미국 실적·경제지표(한국시간 오늘 밤~내일 새벽)."""
    import market_calendar as mc
    day = mc._day(now.date())
    return {"earnings": day.get("earnings_us") or [], "macro": day.get("macro") or []}


def target_ups(now: datetime, limit: int = 5) -> list[dict]:
    """오늘 목표주가 상향 리포트(consensus_report.py가 오전에 올린 consensus/YYYY-MM-DD.json), 상향 폭 큰 순.
    현재가(네이버 증권, 장 마감 뒤면 종가)와 목표주가까지 상승여력을 붙인다."""
    try:
        items = json.loads((ROOT / "consensus" / f"{now:%Y-%m-%d}.json").read_text(encoding="utf-8")).get("items") or []
    except (OSError, ValueError):
        return []
    ups = sorted((i for i in items if (i.get("pct") or 0) > 0 and i.get("target")), key=lambda i: -i["pct"])
    from market_issues import HEADERS
    with httpx.Client(headers=HEADERS, timeout=10) as http:
        for i in ups[:limit]:
            try:
                j = http.get(f"https://m.stock.naver.com/api/stock/{i['code']}/basic").json()
                i["price"] = int(str(j["closePrice"]).replace(",", ""))
                i["upside"] = (i["target"] / i["price"] - 1) * 100
            except Exception as e:  # 현재가를 못 받으면 상승여력만 빼고 읽는다
                print("현재가 실패:", i.get("company"), type(e).__name__, e, file=sys.stderr)
    for i in ups:
        i["more"] = len(ups) - limit
    return ups[:limit]


EXTRA = re.compile(r"^(.*?)\s\(([^()]*(?:예상|실제|이전)[^()]*)\)$")


def event_text(item: str) -> str:
    """'★TSMC (EPS 예상 $4.45)' -> 'TSMC, 주당순이익 예상 4.45달러',
    '★미 9월 소비자물가(CPI) (예상 3.6%, 이전 3.4% / ...)' -> '미국 9월 소비자물가, 예상 3.6%',
    '연준 베이지북(한국 15일 새벽 3시)' -> '연준 베이지북, 한국 시간 15일 새벽 3시'."""
    item = item.lstrip("★")
    extra = ""
    m = EXTRA.match(item)
    if m:
        item = m.group(1)
        e = re.search(r"(?:\S+\s+)?예상\s+[^,/]+", m.group(2))
        extra = e.group(0).strip() if e else ""
    item = re.sub(r"\(한국\s*", "(한국 시간 ", item)
    item = re.sub(r"\(([^)]*\d+시[^)]*)\)", r", \1", item)  # 발표 시각은 살린다
    item = re.sub(r"\(([A-Za-z/]+)\)", "", item)  # (CPI), (LCC)
    item = re.sub(r"\((?!~)[^)]*\)", "", item)  # 그 밖의 괄호 설명은 뺀다 ((~16일)은 speakable이 읽는다)
    for a, b in NAMES.items():
        item = re.sub(rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z])", b, item)
    text = speakable(item)
    return f"{text}, {speakable(extra)}" if extra else text


def theme_name(name: str) -> str:
    """'피팅(관이음쇠)/밸브' -> '피팅, 밸브', '항공/저가 항공사(LCC)' -> '항공, 저가 항공사'."""
    return speakable(re.sub(r"\([^)]*\)", "", name).replace("/", ", "))


# ---------- 원고 ----------
def build_script(api: dict, now: datetime, reasons: list[str], issues: list[str], notes: str | None, cal: dict,
                 ups: list[dict]) -> str:
    market = api.get("/api/market") or {}
    macro = api.get("/api/macro") or {}
    idx = {i.get("id"): i for i in market.get("indices", [])}
    inv = {i["name"]: i for i in market.get("investors", [])}
    kospi, kosdaq, nq = idx.get("KGG01P"), idx.get("QGG01P"), idx.get("CME@NQ")
    wd = "월화수목금토일"
    out: list[str] = []
    say = out.append
    P, GAP = PAUSE, PAUSE * 2

    say(f"{HOST}입니다. {now.month}월 {now.day}일 {wd[now.weekday()]}요일 장마감 브리핑 들어보시죠.")

    # 1) 시장 지수 등락률
    parts = []
    for name, i in (("코스피", kospi), ("코스닥", kosdaq)):
        if i and i.get("value") is not None and i.get("change_pct") is not None:
            c = i["change_pct"]
            parts.append(f"{name}{josa(name)} {pct(c)} {word(c)}한 {i['value']:.2f}포인트" if abs(c) >= 0.005
                         else f"{name}{josa(name)} 보합인 {i['value']:.2f}포인트")
    if parts:
        say(f"{GAP}먼저 시장 지수입니다.{P}" + ", ".join(parts) + "로 마감했습니다.")
    extra = []
    usd = macro.get("usd_krw") or {}
    if usd.get("rate") is not None and usd.get("change") is not None:
        ch = usd["change"]
        extra.append(f"원 달러 환율은 {usd['rate']:.1f}원으로 {abs(ch):.1f}원 " + word(ch, "올랐", "내렸", "변동이 없었") + "고")
    tsy = macro.get("treasury_10y") or {}
    if tsy.get("yield") is not None and tsy.get("change") is not None:
        bp = tsy["change"] * 100
        extra.append(f"국고채 10년물 금리는 {tsy['yield']:.3f}%로 {abs(bp):.1f} 베이시스포인트 "
                     + ("올랐" if bp >= 0.05 else "내렸" if bp <= -0.05 else "변동이 없었") + "고")
    if nq and nq.get("change_pct") is not None:
        extra.append(f"나스닥 선물은 {pct(nq['change_pct'])} {word(nq['change_pct'])} 중입니다")
    if extra:
        s = ", ".join(extra)
        say((s if s.endswith("입니다") else s[:-1] + "습니다") + ".")
    breadth = market.get("breadth") or {}
    b = breadth.get("kospi") or {}
    if b:
        up, down = (b.get("up") or 0) + (b.get("ulmt") or 0), (b.get("dwn") or 0) + (b.get("llmt") or 0)
        say(f"코스피는 {up:.0f}종목이 오르고 {down:.0f}종목이 내렸습니다.")

    # 2) 투자자별 수급
    def flow(key: str, subj: bool = False) -> str:
        bits = []
        for name, label in (("외국인", "외국인"), ("기관계", "기관"), ("개인", "개인")):
            v = (inv.get(name) or {}).get(key)
            if v:
                bits.append(f"{label}{josa(label, '이', '가') if subj else josa(label)} {eok(v)} {'순매수' if v > 0 else '순매도'}")
        return ", ".join(bits)

    if inv:
        say(f"{GAP}투자자별 수급입니다.{P}")
        for label, key in (("코스피", "kospi"), ("코스닥", "kosdaq")):
            f = flow(key)
            if f:
                say(f"{label}에서 {f}했습니다.")
        fut = flow("futures", subj=True)
        if fut:
            say(f"코스피 200 선물은 {fut}했습니다.")
        fs, ff = (inv.get("외국인") or {}).get("kospi"), (inv.get("외국인") or {}).get("futures")
        if fs and ff:
            w = lambda v: "순매수" if v > 0 else "순매도"  # noqa: E731
            say(f"{P}정리하면 외국인은 현물과 선물을 함께 {w(fs)}했습니다." if (fs > 0) == (ff > 0)
                else f"{P}정리하면 외국인은 현물은 {w(fs)}, 선물은 {w(ff)}했습니다.")

    # 3) 오늘 상승·하락 요인
    if reasons or issues or notes:
        c = (kospi or {}).get("change_pct") or 0
        say(f"{GAP}오늘 시장의 {'상승' if c > 0.005 else '하락' if c < -0.005 else '등락'} 요인입니다.{P}")
        if reasons:
            rs = [speakable(r) for r in reasons]
            say("마감 시황 기사들이 꼽은 이유는 " + ", ".join(rs[:-1]) + (", 그리고 " if len(rs) > 1 else "") + rs[-1] + "입니다.")
        if issues:
            say(f"{P}오늘 기사가 가장 많이 몰린 이슈는 " + ", 그리고 ".join(speakable(t) for t in issues) + "입니다.")
        if notes:
            say(f"{P}자세히 짚어 보겠습니다.{P}{notes}")

    # 4) 강세 테마
    th = (api.get("/api/themes") or {}).get("themes") or []
    if th:
        say(f"{GAP}오늘의 강세 테마입니다.{P}")
        for n, t in enumerate(th[:THEMES_READ], 1):
            s = f"{n}위, {theme_name(t['name'])}"
            if t.get("change_pct") is not None:
                s += f" {pct(t['change_pct'], 1)} {word(t['change_pct'])}"
            leaders = [ld for ld in t.get("leaders") or [] if ld.get("change_pct") is not None][:2]
            if leaders:
                s += ". " + ", ".join(f"{ld['name']} {pct(ld['change_pct'], 1)}" for ld in leaders)
                s += " 올랐습니다." if all(ld["change_pct"] > 0 for ld in leaders) else "입니다."
            else:
                s += "."
            say(s)

    # 5) 목표주가 상향 리포트: 종목·증권사·상향 폭 -> 리포트 제목 -> 현재가 대비 상승여력
    if ups:
        say(f"{GAP}오늘 목표주가 상향 리포트입니다.{P}")
        for u in ups:
            s = (f"{u['company']}. {speakable(u['broker'])}{josa(speakable(u['broker']), '이', '가')} 목표주가를 {u['prev_target']}원에서 {u['target']}원으로 "
                 f"{pct(u['pct'], 1)} 올렸습니다.")
            if u.get("title"):
                s += f" 리포트 제목은 {speakable(u['title'])}."
            if u.get("upside") is not None:
                s += (f" 현재 주가 {u['price']}원 기준 상승여력은 {pct(u['upside'], 1)}입니다." if u["upside"] >= 0
                      else f" 현재 주가 {u['price']}원은 이미 목표주가를 {pct(u['upside'], 1)} 웃돌고 있습니다.")
            say(s + P)
        if ups[0]["more"] > 0:
            say(f"이 밖에 {ups[0]['more']}건의 상향 리포트가 더 있었습니다.")

    # 6) 오늘 저녁 미국 주요 일정·실적 발표
    earn = [event_text(e) for e in sorted(cal["earnings"], key=lambda e: not e.startswith("★"))]
    mac = [event_text(e) for e in sorted(cal["macro"], key=lambda e: not e.startswith("★"))]
    say(f"{GAP}오늘 저녁 미국 일정입니다.{P}")
    if mac:
        say("오늘 밤 주요 지표와 이슈는 " + ". ".join(mac) + "입니다.")
    if earn:
        say(f"{P}오늘 밤부터 내일 새벽 사이 실적을 발표하는 기업은 " + ". ".join(earn) + "입니다.")
    if not mac and not earn:
        say("오늘 밤 예정된 주요 미국 일정과 실적 발표는 없습니다.")

    say(f"{GAP}이상 {HOST}이었습니다. 오늘 하루도 수고 많으셨습니다. 안전 운전하세요.")
    return "\n".join(out)


# ---------- 음성 ----------
# ElevenLabs(내 목소리): ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID가 있으면 먼저 쓰고, 없거나 실패하면 edge-tts.
ELEVEN_MODEL = "eleven_multilingual_v2"
ELEVEN_FORMAT, ELEVEN_KBPS = "mp3_44100_64", 64
EDGE_KBPS = 48
SILENCE = ROOT / "assets" / "silence_0.5s.mp3"  # edge-tts 출력과 같은 24kHz·48kbps 모노 MP3


def eleven_tts(text: str, path: Path) -> bool:
    key, voice = os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        return False
    try:
        r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format={ELEVEN_FORMAT}",
                       json=dict(text=text, model_id=os.environ.get("ELEVENLABS_MODEL") or ELEVEN_MODEL,
                                 voice_settings=dict(stability=0.5, similarity_boost=0.85, style=0.0, use_speaker_boost=True)),
                       headers={"xi-api-key": key, "Accept": "audio/mpeg"}, timeout=180)
    except Exception as e:
        print("내 목소리(ElevenLabs) 실패:", type(e).__name__, file=sys.stderr)
        return False
    if r.status_code != 200:  # 키가 틀렸거나 이번 달 글자 수를 다 썼을 때 등
        print("내 목소리(ElevenLabs) 실패:", r.status_code, r.text[:300], file=sys.stderr)
        return False
    path.write_bytes(r.content)
    return path.stat().st_size > 10000


async def _edge(text: str, path: Path) -> None:
    """원고를 PAUSE 기준으로 나눠 따로 읽히고, 사이에 무음(쉼표 개수 x 0.5초)을 이어 붙인다."""
    import edge_tts
    silence = SILENCE.read_bytes() if SILENCE.exists() else b""
    buf = bytearray()
    for piece in re.split(f"({PAUSE}+)", text):
        if piece.startswith(PAUSE):
            if buf:
                buf += silence * len(piece)
            continue
        piece = piece.strip()
        if not piece:
            continue
        tmp = path.with_suffix(".part.mp3")
        await edge_tts.Communicate(piece, VOICE, rate=RATE, pitch=PITCH).save(str(tmp))
        buf += tmp.read_bytes()
        tmp.unlink(missing_ok=True)
    path.write_bytes(bytes(buf))


def synth(text: str, path: Path, attempts: int = 3) -> int | None:
    """음성 파일을 만들고 비트레이트(kbps)를 돌려준다. 실패하면 None."""
    for a, b in SOUND:
        text = text.replace(a, b)
    if eleven_tts(re.sub(f"{PAUSE}+", "\n\n", text), path):
        print("음성: 내 목소리(ElevenLabs)")
        return ELEVEN_KBPS
    for i in range(attempts):
        try:
            asyncio.run(_edge(text, path))
            if path.exists() and path.stat().st_size > 10000:
                print(f"음성: 기본 음성({VOICE})")
                return EDGE_KBPS
        except Exception as e:
            print(f"TTS 실패({i + 1}/{attempts}):", type(e).__name__, e, file=sys.stderr)
    return None


def mp3_seconds(path: Path, kbps: int) -> int:
    return round(path.stat().st_size * 8 / (kbps * 1000))


# ---------- 팟캐스트 피드 ----------
def update_feed(pod: Path, mp3: Path, text: str, now: datetime, kbps: int) -> None:
    ep_dir = pod / "episodes"
    ep_dir.mkdir(parents=True, exist_ok=True)
    # 파일명·제목·guid를 회차마다 다르게: 팟캐스트 앱은 주소나 제목이 같으면 예전 파일을 계속 튼다
    name = f"{now:%Y-%m-%d-%H%M}.mp3"
    shutil.copyfile(mp3, ep_dir / name)
    meta_p = pod / "episodes.json"
    try:
        eps = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        eps = []
    wd = "월화수목금토일"[now.weekday()]
    title = f"{now.month}/{now.day}({wd}) 장마감 브리핑"
    same_day = [e for e in eps if e["file"].startswith(f"{now:%Y-%m-%d}")]
    if same_day:  # 같은 날 다시 만들면 앞의 것을 지우고 새 회차로 바꿔 끼운다
        title += f" ({now:%H:%M} 업데이트)"
        for e in same_day:
            if e["file"] != name:
                (ep_dir / e["file"]).unlink(missing_ok=True)
        eps = [e for e in eps if e not in same_day]
    pub = now.replace(tzinfo=KST)
    eps.append(dict(file=name, title=title, pub=pub.isoformat(),
                    bytes=(ep_dir / name).stat().st_size, secs=mp3_seconds(ep_dir / name, kbps),
                    desc=text.split("\n")[1] if "\n" in text else text,
                    guid=str(uuid.uuid5(uuid.NAMESPACE_URL, f"close/{name}@{pub.isoformat()}"))))
    eps.sort(key=lambda e: e["pub"], reverse=True)
    for old in eps[KEEP:]:
        (ep_dir / old["file"]).unlink(missing_ok=True)
    eps = eps[:KEEP]
    meta_p.write_text(json.dumps(eps, ensure_ascii=False, indent=1), encoding="utf-8")

    items = "".join(f'''
  <item>
   <title>{escape(e["title"])}</title>
   <description>{escape(e["desc"])}</description>
   <pubDate>{format_datetime(datetime.fromisoformat(e["pub"]))}</pubDate>
   <guid isPermaLink="false">{e["guid"]}</guid>
   <enclosure url="{POD_BASE}/episodes/{e["file"]}" length="{e["bytes"]}" type="audio/mpeg"/>
   <itunes:duration>{e["secs"]}</itunes:duration>
  </item>''' for e in eps)
    feed = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom">
 <channel>
  <title>{SHOW_TITLE}</title>
  <link>https://github.com/Sunbong-oh/kb-market-report</link>
  <atom:link href="{FEED_URL}" rel="self" type="application/rss+xml"/>
  <language>ko-kr</language>
  <description>평일 오후 4시, 코스피·코스닥 등락률, 투자자별 수급, 오늘의 상승·하락 요인, 강세 테마, 목표주가 상향 리포트, 오늘 밤 미국 일정·실적을 음성으로 짚어 드립니다. 투자 판단의 참고용입니다.</description>
  <itunes:author>kb-market-report</itunes:author>
  <itunes:explicit>false</itunes:explicit>
  <itunes:block>Yes</itunes:block>
  <lastBuildDate>{format_datetime(pub)}</lastBuildDate>{items}
 </channel>
</rss>
'''
    (pod / "feed.xml").write_text(feed, encoding="utf-8")
    print(f"피드 갱신: {len(eps)}회차, 최신 {name}")


# ---------- 텔레그램 ----------
def send_audio(mp3: Path, title: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("텔레그램 설정 없음 -> 음성 발송 건너뜀", file=sys.stderr)
        return
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendAudio",
                       data={"chat_id": chat, "title": title, "performer": SHOW_TITLE},
                       files={"audio": (mp3.name, mp3.read_bytes(), "audio/mpeg")}, timeout=90)
        ok = r.status_code == 200 and r.json().get("ok")
    except Exception as e:  # 토큰이 URL에 있어 예외 메시지는 출력하지 않는다
        print("텔레그램 음성 발송 실패:", type(e).__name__, file=sys.stderr)
        ok = False
    print(f"텔레그램 음성 발송 {'완료' if ok else '실패'}")


def main() -> int:
    src = Path(sys.argv[sys.argv.index("--html") + 1]) if "--html" in sys.argv else SNAPSHOT
    if not src.exists():
        print(f"장마감 리포트 스냅샷이 없습니다({src}) - 휴장일이거나 리포트가 만들어지지 않았습니다. 건너뜀.")
        return 0
    api, now = load_snapshot(src)
    rows = news_rows(now)
    reasons = market_reasons(rows)
    issues = hot_issues(rows, reasons)
    try:
        cal = tonight_us(now)
    except Exception as e:  # 캘린더가 없어도 나머지 브리핑은 나간다
        print("캘린더 실패:", type(e).__name__, e, file=sys.stderr)
        cal = {"earnings": [], "macro": []}
    try:
        ups = target_ups(now)
    except Exception as e:  # 목표주가 칸이 빠져도 나머지 브리핑은 나간다
        print("목표주가 상향 실패:", type(e).__name__, e, file=sys.stderr)
        ups = []
    text = build_script(api, now, reasons, issues, close_notes(now), cal, ups)
    plain = re.sub(f"{PAUSE}+", " ", text).replace(" \n", "\n").strip()
    TXT.parent.mkdir(exist_ok=True)
    TXT.write_text(plain, encoding="utf-8")
    print(f"원고 {len(plain)}자 (약 {len(plain) / 330:.1f}분)\n{plain}")
    if "--text-only" in sys.argv:
        return 0
    kbps = synth(text, MP3)
    if not kbps:
        print("음성 생성 실패", file=sys.stderr)
        return 1
    secs = mp3_seconds(MP3, kbps)
    print(f"음성 생성: {MP3} ({MP3.stat().st_size // 1024}KB, 약 {secs // 60}분 {secs % 60}초)")
    if "--pod" in sys.argv:
        update_feed(Path(sys.argv[sys.argv.index("--pod") + 1]), MP3, plain, now, kbps)
    if "--notify" in sys.argv:
        send_audio(MP3, f"{now.month}/{now.day} 퇴근길 장마감 브리핑")
    return 0


if __name__ == "__main__":
    sys.exit(main())
