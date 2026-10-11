"""제주 아이 행사 알리미 - 4살 아이가 신청할 만한 제주 프로그램·행사를 모아 사이트를 만들고 텔레그램으로 알린다.

하는 일 (GitHub Actions가 매일 아침 6시·저녁 8시에 실행):
  1) events.json(직접 관리하는 일정)에서 신청 시작 3일 전 / 전날 / 당일 아침, 마감 전날을 알린다.
  2) sources.json의 기관 누리집과 뉴스 검색을 훑어 유아·가족 대상 새 공고를 찾으면 바로 알린다.
     글에 '신청·접수·모집'과 날짜가 있으면 그 날짜로도 알림을 건다(자동 감지 표시).
  3) 모은 내용을 site/index.html 로 만든다 (GitHub Pages).
  4) 월요일 아침에는 앞으로 2주 신청 일정을 한 번에 보낸다.

필요한 환경변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (KIDS_TELEGRAM_CHAT_ID가 있으면 그쪽으로 보냄)
실행: python jeju-kids/kids.py [--no-send] [--no-crawl] [--now 2026-10-11T06:00]
"""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urljoin

import httpx

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "state"  # 루트 .gitignore의 data/ 와 겹치지 않게
SITE = ROOT / "site"
WEEKDAYS = "월화수목금토일"

# 4살과 관련 있는 글만 고른다. 초·중·고·성인 전용으로 보이는 글은 뺀다.
KID_WORDS = ("유아", "영유아", "미취학", "4세", "5세", "3~5세", "만3", "만4", "가족", "부모", "어린이", "아동", "키즈", "그림책", "동화")
ADULT_ONLY = ("초등", "중학", "고등", "청소년", "성인", "어르신", "노인", "시니어", "교직원", "교사 연수", "채용", "입찰", "계약")
STRONG_KID = ("유아", "영유아", "미취학", "4세", "3~5세", "만3", "만4", "가족", "부모")
ACTION_WORDS = ("모집", "신청", "접수", "예약", "공연", "체험", "교실", "축제", "축전", "행사", "프로그램", "강좌", "운영")
CATEGORIES = {
    "영어": ("영어", "english", "잉글리시"),
    "미술": ("미술", "그리기", "만들기", "공예", "아트", "색칠", "클레이"),
    "체육": ("체육", "운동", "놀이체육", "줄넘기", "수영", "축구", "발레", "댄스", "몸놀이"),
    "요리": ("요리", "쿠킹", "베이킹", "쿠키", "제빵"),
    "독서": ("책", "그림책", "동화", "독서", "구연", "도서관"),
    "공연": ("공연", "인형극", "뮤지컬", "음악회", "연극", "콘서트"),
    "마술": ("마술", "매직"),
    "과학": ("과학", "천문", "로봇", "코딩", "실험"),
    "축제": ("축제", "축전", "페스티벌"),
    "체험": ("체험", "숲", "생태", "농장"),
}


# ── 날짜 ─────────────────────────────────────────────

def parse_dt(s: str | None, end: bool = False) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s)
    if len(s) <= 10:  # 날짜만 있으면 시작은 00:00, 마감은 23:59
        d = d.replace(hour=23, minute=59) if end else d
    return d.replace(tzinfo=KST) if d.tzinfo is None else d


def fmt_day(d: datetime | date) -> str:
    return f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"


def fmt_dt(d: datetime) -> str:
    return fmt_day(d) + (f" {d:%H:%M}" if (d.hour, d.minute) != (0, 0) else "")


FULL_DATE = re.compile(r"(20\d\d)\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
DAY_FROM = re.compile(r"(?<![\d월.])(\d{1,2})\s*일\s*(?:부터|오전|오후|\d{1,2}시)")
TIME = re.compile(r"(오전|오후)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?")


def find_dates(text: str, ref: date) -> list[date]:
    """글에 나온 날짜들. 연도가 없으면 ref(글 작성일) 기준으로 가장 가까운 앞날로 본다."""
    out: list[date] = []

    def add(y, m, d):
        try:
            out.append(date(y, m, d))
        except ValueError:
            pass

    for y, m, d in FULL_DATE.findall(text):
        add(int(y), int(m), int(d))
    rest = FULL_DATE.sub(" ", text)
    for m, d in MONTH_DAY.findall(rest):
        y = ref.year + (1 if int(m) < ref.month - 6 else 0)
        add(y, int(m), int(d))
    for (d,) in [(x,) for x in DAY_FROM.findall(MONTH_DAY.sub(" ", rest))]:
        y, m = ref.year, ref.month
        if int(d) < ref.day - 3:  # '8일부터'가 글 작성일보다 한참 앞이면 다음 달
            m += 1
            if m == 13:
                y, m = y + 1, 1
        add(y, m, int(d))
    return sorted(set(out))


def guess_apply_start(text: str, ref: date) -> datetime | None:
    """'8일 오전 10시부터 신청', '10.13.(월) 09:00 접수' 같은 문장에서 신청 시작 시각을 찾는다."""
    for m in re.finditer(r"(신청|접수|모집|예약)", text):
        window = text[max(0, m.start() - 40): m.end() + 70]
        ds = find_dates(window, ref)
        if not ds:
            continue
        hh, mm = 0, 0
        t = TIME.search(window) or re.search(r"(\d{1,2}):(\d{2})", window)
        if t and t.re is TIME:
            hh, mm = int(t.group(2)), int(t.group(3) or 0)
            hh += 12 if t.group(1) == "오후" and hh < 12 else 0
        elif t:
            hh, mm = int(t.group(1)), int(t.group(2))
        if hh > 23:
            hh, mm = 0, 0
        d = ds[0]
        return datetime(d.year, d.month, d.day, hh, mm, tzinfo=KST)
    return None


def categorize(text: str) -> list[str]:
    low = text.lower()
    return [c for c, words in CATEGORIES.items() if any(w in low for w in words)] or ["기타"]


def is_for_little_kids(text: str) -> bool:
    if not any(w in text for w in KID_WORDS):
        return False
    if any(w in text for w in ADULT_ONLY) and not any(w in text for w in STRONG_KID):
        return False
    return any(w in text for w in ACTION_WORDS)


# ── 수집 ─────────────────────────────────────────────

class LinkRows(HTMLParser):
    """페이지에서 (글 줄 텍스트, 링크) 목록을 뽑는다. 표의 한 줄(tr)·목록 한 칸(li)을 한 글로 본다."""

    BLOCKS = {"tr", "li", "dl", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, str]] = []
        self.stack: list[dict] = []
        self.skip = 0
        self.href_onclick = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.stack.append({"text": [], "href": None})
        elif tag == "a":
            href = a.get("href") or ""
            if href.startswith("javascript") or href in ("", "#"):
                href = ""
            cur = self.stack[-1] if self.stack else None
            if cur is None:
                self.stack.append({"text": [], "href": href, "anchor_only": True})
            elif href and not cur["href"]:
                cur["href"] = href

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)
        elif (tag in self.BLOCKS or tag == "a") and self.stack:
            top = self.stack[-1]
            if tag == "a" and not top.get("anchor_only"):
                return
            self.stack.pop()
            text = " ".join(" ".join(top["text"]).split())
            if 8 <= len(text) <= 300:
                self.rows.append((text, top["href"] or ""))
            if self.stack:  # 바깥 블록에도 글자를 남긴다
                self.stack[-1]["text"].append(text)

    def handle_data(self, data):
        if not self.skip and self.stack:
            self.stack[-1]["text"].append(data)


def item_id(source: str, title: str) -> str:
    norm = re.sub(r"\s+|\d{4}[.\-]\d{1,2}[.\-]\d{1,2}|조회\s*\d+|\[?(접수중|마감|대기)\]?", "", title)
    return hashlib.sha1(f"{source}|{norm}".encode()).hexdigest()[:12]


def fetch(client: httpx.Client, url: str) -> str:
    try:
        r = client.get(url)
    except httpx.ConnectError:  # 일부 관공서 인증서 체인 문제 → 공개 페이지 읽기만 하므로 검증 없이 재시도
        with httpx.Client(verify=False, timeout=client.timeout, headers=client.headers, follow_redirects=True) as c2:
            r = c2.get(url)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
        m = re.search(rb'charset=["\']?([\w-]+)', r.content[:2000])
        r.encoding = m.group(1).decode() if m else "utf-8"
    return r.text


def crawl_html(client, src, today) -> list[dict]:
    p = LinkRows()
    p.feed(fetch(client, src["url"]))
    items, seen = [], set()
    for text, href in p.rows:
        if not is_for_little_kids(text):
            continue
        iid = item_id(src["name"], text)
        if iid in seen:
            continue
        seen.add(iid)
        items.append({
            "id": iid, "source": src["name"], "title": text[:160],
            "url": urljoin(src["url"], href) if href else src["url"],
            "published": today.isoformat(), "text": text,
        })
    return items


def crawl_rss(client, query, today) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote(query + ' when:30d')}&hl=ko&gl=KR&ceid=KR:ko"
    root = ET.fromstring(fetch(client, url))
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))
        text = f"{title} {desc}"
        if "제주" not in text or not is_for_little_kids(text):
            continue
        try:
            pub = email.utils.parsedate_to_datetime(it.findtext("pubDate")).astimezone(KST).date()
        except (TypeError, ValueError):
            pub = today
        if (today - pub).days > 30:
            continue
        # 같은 보도자료가 여러 매체에 실리므로 매체명을 떼고 제목으로 묶는다
        clean = re.sub(r"\s+-\s+[^-]+$", "", title)
        clean = re.sub(r"^\[[^\]]+\]\s*", "", clean)
        items.append({
            "id": item_id("news", clean), "source": "뉴스", "title": clean,
            "url": it.findtext("link") or "", "published": pub.isoformat(), "text": text,
        })
    return items


def crawl(sources: dict, today: date) -> tuple[list[dict], list[dict]]:
    headers = {"User-Agent": "Mozilla/5.0 (jeju-kids-alert; personal family calendar)"}
    found, health = {}, []
    with httpx.Client(timeout=20, headers=headers, follow_redirects=True) as client:
        jobs = [("html", s["name"], s) for s in sources.get("html", [])]
        jobs += [("rss", f"뉴스: {q}", q) for q in sources.get("rss", [])]
        for kind, name, arg in jobs:
            try:
                items = crawl_html(client, arg, today) if kind == "html" else crawl_rss(client, arg, today)
                health.append({"name": name, "ok": True, "count": len(items)})
                for it in items:
                    found.setdefault(it["id"], it)
            except Exception as e:  # 한 곳이 막혀도 나머지는 계속
                health.append({"name": name, "ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}"})
    return list(found.values()), health


# ── 상태 · 알림 ──────────────────────────────────────

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def enrich(item: dict) -> dict:
    ref = date.fromisoformat(item["published"])
    start = guess_apply_start(item["text"], ref)
    item["categories"] = categorize(item["text"])
    item["apply_start"] = start.isoformat(timespec="minutes") if start else None
    item["dates"] = [d.isoformat() for d in find_dates(item["text"], ref)][:6]
    return item


def reminders(events: list[dict], now: datetime, sent: dict) -> list[tuple[str, str, dict]]:
    """보낼 알림 (키, 문구, 일정). 실행이 한 번 빠져도 다음 실행에서 놓친 단계를 보낸다."""
    out = []
    today = now.date()
    for ev in events:
        start, end = parse_dt(ev.get("apply_start")), parse_dt(ev.get("apply_end"), end=True)
        if start and now < start:
            days = (start.date() - today).days
            when = fmt_dt(start)
            if days == 0:
                stage, msg = "d0", f"⏰ 오늘 {start:%H:%M} 신청 시작" if start.hour else "⏰ 오늘 신청 시작"
            elif days == 1:
                stage, msg = "d1", f"🔔 내일 {when} 신청 시작 — 알람 맞춰 두세요"
            elif days <= 3:
                stage, msg = "d3", f"📌 {days}일 뒤 {when} 신청 시작"
            else:
                stage = None
            if stage and f"{ev['id']}:{stage}" not in sent:
                out.append((f"{ev['id']}:{stage}", msg, ev))
        if end and now < end and (end.date() - today).days <= 1:
            key = f"{ev['id']}:end"
            if key not in sent:
                out.append((key, f"⌛ 신청 마감 {fmt_dt(end)} — 아직 안 했으면 지금!", ev))
    return out


def describe(ev: dict) -> str:
    lines = [f"<b>{html.escape(ev['title'])}</b>"]
    if ev.get("auto"):
        lines.append("※ 자동 감지한 날짜예요. 원문에서 꼭 확인하세요")
    for label, key in (("대상", "age"), ("장소", "place"), ("일정", "schedule"), ("방법", "how")):
        if ev.get(key):
            lines.append(f"{label}: {html.escape(ev[key])}")
    s, e = parse_dt(ev.get("event_start")), parse_dt(ev.get("event_end"))
    if s:
        lines.append(f"기간: {fmt_day(s)}" + (f" ~ {fmt_day(e)}" if e else ""))
    if ev.get("url"):
        lines.append(f'<a href="{html.escape(ev["url"], quote=True)}">신청·안내 페이지</a>')
    return "\n".join(lines)


def send(text: str, dry: bool) -> None:
    if dry:
        print("── 텔레그램(미전송) ──\n" + re.sub(r"<[^>]+>", "", text) + "\n")
        return
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ.get("KIDS_TELEGRAM_CHAT_ID") or os.environ["TELEGRAM_CHAT_ID"]
    for i in range(0, len(text), 3800):  # 텔레그램 4096자 제한
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, data={
            "chat_id": chat, "text": text[i:i + 3800], "parse_mode": "HTML", "disable_web_page_preview": "true"})
        if r.status_code != 200 or not r.json().get("ok"):
            raise RuntimeError(f"텔레그램 전송 실패: HTTP {r.status_code} {r.text[:200]}")


def weekly_digest(events: list[dict], now: datetime) -> str:
    rows = []
    for ev in events:
        s = parse_dt(ev.get("apply_start"))
        if s and now <= s <= now + timedelta(days=14):
            rows.append((s, f"• {fmt_dt(s)} 신청 시작 — {html.escape(ev['title'])}"))
        e = parse_dt(ev.get("apply_end"), end=True)
        if e and now <= e <= now + timedelta(days=14):
            rows.append((e, f"• {fmt_dt(e)} 신청 마감 — {html.escape(ev['title'])}"))
    rows.sort(key=lambda r: r[0])
    body = "\n".join(r[1] for r in rows) or "앞으로 2주 안에 확정된 신청 일정은 아직 없어요. 새 공고가 뜨면 바로 알려드릴게요."
    return f"🗓 <b>이번 주 제주 아이 행사 신청 일정</b> ({fmt_day(now)})\n\n{body}"


# ── 사이트 ───────────────────────────────────────────

def build_site(payload: dict) -> None:
    tpl = (ROOT / "template.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    page = tpl.replace("/*__DATA__*/null", data)
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(
        '<!doctype html>\n<html lang="ko"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        "</head><body>\n" + page + "\n</body></html>\n", encoding="utf-8")
    save_json(SITE / "data.json", payload)


# ── 실행 ─────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-send", action="store_true", help="텔레그램 대신 화면에 출력")
    ap.add_argument("--no-crawl", action="store_true", help="누리집·뉴스 수집 생략")
    ap.add_argument("--now", help="시각 지정 (테스트용, 예: 2026-10-11T06:00)")
    args = ap.parse_args()
    dry = args.no_send or not os.environ.get("TELEGRAM_BOT_TOKEN")
    now = parse_dt(args.now) if args.now else datetime.now(KST)
    today = now.date()

    events = load_json(ROOT / "events.json", {})["events"]
    seen: dict = load_json(DATA / "seen.json", {})
    sent: dict = load_json(DATA / "sent.json", {})
    health = load_json(DATA / "health.json", [])
    first_run = not seen

    new_items = []
    if not args.no_crawl:
        found, health = crawl(load_json(ROOT / "sources.json", {}), today)
        for it in found:
            if it["id"] not in seen:
                it["first_seen"] = now.isoformat(timespec="minutes")
                seen[it["id"]] = enrich(it)
                new_items.append(it)
        # 90일 지난 수집 글은 정리
        cutoff = (now - timedelta(days=90)).isoformat()
        seen = {k: v for k, v in seen.items() if v.get("first_seen", "") >= cutoff}

    # 자동 감지한 신청 시작일도 알림 대상에 넣는다
    auto_events = [{
        "id": f"auto-{v['id']}", "title": v["title"], "apply_start": v["apply_start"],
        "url": v["url"], "categories": v["categories"], "auto": True,
    } for v in seen.values() if v.get("apply_start")]
    all_events = events + auto_events

    messages = []
    if new_items and not first_run:
        lines = [f"🆕 <b>새로 올라온 제주 아이 행사 공고 {len(new_items)}건</b>"]
        for it in sorted(new_items, key=lambda x: x["published"], reverse=True)[:12]:
            extra = f" · 신청 {fmt_dt(datetime.fromisoformat(it['apply_start']))}" if it.get("apply_start") else ""
            lines.append(f'\n• <a href="{html.escape(it["url"], quote=True)}">{html.escape(it["title"])}</a>'
                         f"\n  {html.escape(it['source'])} · {'/'.join(it['categories'])}{extra}")
        messages.append("\n".join(lines))
    elif first_run and new_items:
        messages.append(f"👋 제주 아이 행사 알리미를 시작했어요. 지금 올라와 있는 공고 {len(new_items)}건을 사이트에 모았고, "
                        "앞으로 새 공고와 신청 시작일을 알려드릴게요.")

    due = reminders(all_events, now, sent)
    for key, msg, ev in due:
        messages.append(f"{msg}\n{describe(ev)}")

    is_monday_morning = now.weekday() == 0 and now.hour < 12
    if is_monday_morning and f"weekly:{today}" not in sent:
        messages.append(weekly_digest(all_events, now))

    failing = [h["name"] for h in health if not h.get("ok")]
    for msg in messages:
        send(msg, dry)
    if not dry:
        for key, _, _ in due:
            sent[key] = now.isoformat(timespec="minutes")
        if is_monday_morning:
            sent[f"weekly:{today}"] = now.isoformat(timespec="minutes")
    print(f"일정 {len(events)} · 수집 글 {len(seen)} (새 글 {len(new_items)}) · 알림 {len(messages)} · 실패한 곳 {len(failing)}")
    for h in health:
        print(("  ok  " if h.get("ok") else "  !!  ") + h["name"] + (f" ({h['count']}건)" if h.get("ok") else f" — {h['error']}"))

    save_json(DATA / "seen.json", seen)
    save_json(DATA / "sent.json", sent)
    save_json(DATA / "health.json", health)
    build_site({
        "updated": now.isoformat(timespec="minutes"),
        "events": events,
        "found": sorted(({k: v[k] for k in ("id", "source", "title", "url", "published", "first_seen", "categories", "apply_start", "dates") if k in v}
                         for v in seen.values()), key=lambda x: x.get("first_seen", ""), reverse=True),
        "health": health,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
