"""텔레그램 받은편지함 - 인스타그램 게시물 링크·캡처·글을 봇에게 보내면 신청 날짜를 읽어 알림에 넣는다.

인스타그램은 로그인 없이 자동으로 긁으면 금방 막히므로, 보다가 괜찮은 게시물을 봇에게 '공유'하는 방식이다.
  • 게시물 링크  → 공개 게시물의 미리보기(글·대표 사진)를 읽어 본다
  • 캡처 사진    → 포스터 속 날짜·대상·장소를 읽는다 (여러 장을 한 번에 보내도 됨)
  • 글 복사/붙여넣기 → 그대로 읽는다
읽은 결과를 답장으로 보여 주고 state/inbox_events.json 에 저장 → kids.py 가 신청 알림을 건다.

명령: /list 다가오는 신청  ·  /del 번호 지우기  ·  /help

필요한 환경변수
  KIDS_TELEGRAM_BOT_TOKEN  아이 행사 전용 봇 (@BotFather에서 새로 만든 봇). 장마감 리포트 봇은 노트북 서버가
                           이미 메시지를 받고 있어 같이 쓰면 서로 메시지를 빼앗는다.
  KIDS_TELEGRAM_CHAT_ID    (없으면 TELEGRAM_CHAT_ID) 이 대화방에서 온 메시지만 처리
  KIDS_ALLOWED_CHAT_IDS    (선택) 함께 쓸 사람의 chat id, 쉼표로 구분
  ANTHROPIC_API_KEY        (선택) 있으면 Claude가 캡처 사진과 긴 글을 읽는다. 없으면 글 속 날짜만 찾는다.
"""
from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import sys
from datetime import datetime

import httpx

from kids import DATA, KST, categorize, find_dates, fmt_dt, guess_apply_start, load_json, parse_dt, save_json

INBOX = DATA / "inbox_events.json"
OFFSET = DATA / "inbox_offset.json"
IG_URL = re.compile(r"https?://(?:www\.)?instagram\.com/(?:p|reel|reels|tv)/[\w-]+/?[^\s]*", re.I)
URL = re.compile(r"https?://\S+")
MODEL = "claude-opus-5-5"
HELP = ("👶 제주 아이 행사 받은편지함\n\n"
        "인스타·블로그·카톡에서 본 행사를 이 대화방으로 보내 주세요.\n"
        "• 인스타 게시물 링크 (공유 → 텔레그램 또는 링크 복사)\n"
        "• 포스터 캡처 사진 (여러 장 OK)\n"
        "• 모집 글 복사해서 붙여넣기\n\n"
        "신청 날짜를 읽어 3일 전 · 전날 저녁 · 당일 아침 · 시작 1시간 전에 알려 드려요.\n"
        "15분 안에 답장이 와요.\n\n"
        "/list 다가오는 신청 보기\n/del 번호 잘못 읽은 일정 지우기")


class Bot:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self.http = httpx.Client(timeout=40)

    def call(self, method: str, **params):
        r = self.http.post(f"{self.base}/{method}", data=params)
        body = r.json()
        if not body.get("ok"):
            raise RuntimeError(f"텔레그램 {method} 실패: {body}")
        return body["result"]

    def reply(self, chat_id, text: str, to: int | None = None) -> None:
        params = {"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML", "disable_web_page_preview": "true"}
        if to:
            params["reply_to_message_id"] = to
        self.call("sendMessage", **params)

    def photo_bytes(self, photo_sizes: list[dict]) -> bytes:
        file_id = max(photo_sizes, key=lambda p: p.get("file_size", 0) or p["width"])["file_id"]
        path = self.call("getFile", file_id=file_id)["file_path"]
        return self.http.get(f"{self.file_base}/{path}").content


# ── 인스타그램 미리보기 ───────────────────────────────

def instagram_preview(url: str) -> tuple[str, bytes | None]:
    """공개 게시물의 링크 미리보기(og 태그)에서 글과 대표 사진을 가져온다. 막히면 빈 값."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": "facebookexternalhit/1.1", "Accept-Language": "ko-KR,ko"}) as c:
            page = c.get(url).text
            meta = dict(re.findall(r'<meta[^>]+property="og:(\w+)"[^>]+content="([^"]*)"', page))
            text = html.unescape(f"{meta.get('title', '')}\n{meta.get('description', '')}").strip()
            img = c.get(html.unescape(meta["image"])).content if meta.get("image") else None
            return text, img
    except (httpx.HTTPError, KeyError):
        return "", None


# ── 읽기 ─────────────────────────────────────────────

def extract_with_claude(text: str, images: list[bytes], today: datetime) -> list[dict] | None:
    """Claude로 포스터·글에서 프로그램 정보를 뽑는다. 키가 없거나 실패하면 None."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    from pydantic import BaseModel

    class Program(BaseModel):
        title: str
        org: str
        place: str
        age: str
        categories: list[str]
        apply_start: str
        apply_end: str
        event_start: str
        event_end: str
        schedule: str
        fee: str
        how: str
        fit_for_4yo: str

    class Extraction(BaseModel):
        programs: list[Program]

    content: list[dict] = []
    for b in images[:6]:
        kind = "image/png" if b[:4] == b"\x89PNG" else "image/webp" if b[8:12] == b"WEBP" else "image/jpeg"
        content.append({"type": "image", "source": {"type": "base64", "media_type": kind,
                                                    "data": base64.standard_b64encode(b).decode()}})
    content.append({"type": "text", "text": (
        f"오늘은 {today:%Y-%m-%d}(KST)입니다. 아래는 제주에 사는 4살 아이 부모가 인스타그램 등에서 본 행사·프로그램 안내입니다.\n"
        "안내에 나온 프로그램마다 정보를 뽑아 주세요. 행사 안내가 아니면 programs를 빈 목록으로 두세요.\n"
        "- apply_start/apply_end: 신청·접수·예약 시작/마감 시각, 'YYYY-MM-DDTHH:MM' (시각이 없으면 'YYYY-MM-DD'). 연도가 없으면 오늘 이후 가장 가까운 날짜. 모르면 빈 문자열\n"
        "- event_start/event_end: 행사·수업 기간 'YYYY-MM-DD', 모르면 빈 문자열\n"
        "- schedule: 요일·시간 등 운영 일정을 짧게 / age: 대상 연령 / fee: 비용 / how: 신청 방법(누리집, 전화번호 등)\n"
        "- categories: 영어·미술·체육·요리·독서·공연·마술·과학·축제·체험 중 해당하는 것\n"
        "- fit_for_4yo: 4살(만 3~4세)이 참여할 수 있는지 한 줄로\n"
        "글에 없는 내용은 지어내지 말고 빈 문자열로 두세요.\n\n"
        f"안내 글:\n{text or '(글 없음, 사진만 있음)'}")})
    try:
        resp = anthropic.Anthropic().messages.parse(
            model=MODEL, max_tokens=16000, output_format=Extraction,
            messages=[{"role": "user", "content": content}])
    except anthropic.APIError as e:
        print(f"Claude 호출 실패: {e}", file=sys.stderr)
        return None
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return None
    return [p.model_dump() for p in resp.parsed_output.programs]


def extract_simple(text: str, today: datetime) -> list[dict]:
    """키가 없을 때: 글에서 신청 시작일과 날짜만 찾는다."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not URL.fullmatch(ln.strip())]
    if not lines:
        return []
    start = guess_apply_start(text, today.date())
    dates = [d for d in find_dates(text, today.date()) if d >= today.date()]
    return [{
        "title": lines[0][:80], "categories": categorize(text),
        "apply_start": start.isoformat(timespec="minutes")[:16] if start else "",
        "event_start": dates[-1].isoformat() if dates and not start else "",
        "schedule": " ".join(lines[1:3])[:120],
    }]


def clean_dt(s: str) -> str | None:
    try:
        return parse_dt(s) and s[:16] if s else None
    except ValueError:
        return None


def to_event(p: dict, url: str, now: datetime) -> dict:
    ev = {
        "id": "in-" + hashlib.sha1(f"{p.get('title')}|{p.get('apply_start')}|{p.get('event_start')}".encode()).hexdigest()[:8],
        "title": p.get("title") or "제목 없는 행사",
        "categories": [c for c in p.get("categories", []) if c] or categorize(p.get("title", "")),
        "source": url or None, "url": url or None, "added": now.isoformat(timespec="minutes"),
    }
    for k in ("org", "place", "age", "schedule", "fee", "how"):
        if p.get(k):
            ev[k] = p[k]
    if p.get("fit_for_4yo"):
        ev["note"] = p["fit_for_4yo"]
    for k in ("apply_start", "apply_end", "event_start", "event_end"):
        if v := clean_dt(p.get(k, "")):
            ev[k] = v
    ev["kind"] = "dated" if ev.get("apply_start") else "ongoing" if ev.get("event_start") else "watch"
    return {k: v for k, v in ev.items() if v is not None}


def summary(ev: dict, num: int) -> str:
    out = [f"<b>{num}. {html.escape(ev['title'])}</b>"]
    if s := parse_dt(ev.get("apply_start")):
        out.append(f"🔔 신청 시작 {fmt_dt(s)} → 알림 등록")
    else:
        out.append("신청 날짜를 못 찾았어요 (행사 기간만 표시)")
    if e := parse_dt(ev.get("apply_end"), end=True):
        out.append(f"⌛ 마감 {fmt_dt(e)}")
    if s := parse_dt(ev.get("event_start")):
        e = parse_dt(ev.get("event_end"))
        out.append(f"📅 {fmt_dt(s)}" + (f" ~ {fmt_dt(e)}" if e else ""))
    for label, k in (("대상", "age"), ("장소", "place"), ("일정", "schedule"), ("신청", "how"), ("4살", "note")):
        if ev.get(k):
            out.append(f"{label}: {html.escape(ev[k])}")
    return "\n".join(out)


# ── 실행 ─────────────────────────────────────────────

def main() -> int:
    token = os.environ.get("KIDS_TELEGRAM_BOT_TOKEN")
    if not token:
        print("KIDS_TELEGRAM_BOT_TOKEN 이 없어 받은편지함을 건너뜀")
        return 0
    allowed = {os.environ.get("KIDS_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")}
    allowed |= {c.strip() for c in os.environ.get("KIDS_ALLOWED_CHAT_IDS", "").split(",") if c.strip()}
    bot, now = Bot(token), datetime.now(KST)
    offset = load_json(OFFSET, {}).get("offset", 0)
    updates = bot.call("getUpdates", offset=offset, timeout=0, allowed_updates='["message"]')
    if not updates:
        return 0

    inbox: list[dict] = load_json(INBOX, [])
    # 앨범(여러 장)은 media_group_id 로 묶어 한 번에 읽는다
    groups: dict[str, list[dict]] = {}
    for u in updates:
        msg = u.get("message")
        if msg:
            groups.setdefault(msg.get("media_group_id") or f"m{msg['message_id']}", []).append(msg)

    for msgs in groups.values():
        first, chat_id = msgs[0], msgs[0]["chat"]["id"]
        if str(chat_id) not in allowed:
            bot.reply(chat_id, f"개인용 봇이에요. 함께 쓰려면 이 번호를 KIDS_ALLOWED_CHAT_IDS에 넣어 주세요: {chat_id}")
            continue
        text = "\n".join(m.get("text") or m.get("caption") or "" for m in msgs).strip()
        cmd = text.split()[0].lower() if text.startswith("/") else ""
        try:
            if cmd in ("/start", "/help"):
                bot.reply(chat_id, HELP)
                continue
            if cmd == "/list":
                up = sorted((e for e in inbox if (s := parse_dt(e.get("apply_start"))) and s > now),
                            key=lambda e: e["apply_start"])
                bot.reply(chat_id, "\n".join(f"{inbox.index(e) + 1}. {fmt_dt(parse_dt(e['apply_start']))} {html.escape(e['title'])}"
                                             for e in up) or "받은편지함에 다가오는 신청이 없어요. (기관 공고 일정은 사이트에서 확인)")
                continue
            if cmd == "/del":
                nums = [int(n) for n in re.findall(r"\d+", text)]
                gone = [inbox[n - 1]["title"] for n in nums if 0 < n <= len(inbox)]
                inbox = [e for i, e in enumerate(inbox, 1) if i not in nums]
                bot.reply(chat_id, "지웠어요: " + ", ".join(gone) if gone else "번호를 찾지 못했어요. /list 로 확인해 주세요.")
                continue

            ig = IG_URL.search(text)
            url = ig.group(0) if ig else (URL.search(text).group(0) if URL.search(text) else "")
            images = [bot.photo_bytes(m["photo"]) for m in msgs if m.get("photo")]
            if ig and len(URL.sub("", text).strip()) < 20:  # 링크만 왔으면 게시물 미리보기를 읽는다
                ig_text, ig_img = instagram_preview(url)
                text = f"{text}\n{ig_text}".strip()
                if ig_img:
                    images.append(ig_img)
            if not URL.sub("", text).strip() and not images:
                bot.reply(chat_id, "인스타 게시물을 열 수 없었어요 (비공개이거나 인스타가 막음). 포스터를 캡처해서 보내 주시거나 글을 복사해 붙여 주세요.", first["message_id"])
                continue

            programs = extract_with_claude(text, images, now)
            if programs is None:
                if images and not URL.sub("", text).strip():
                    bot.reply(chat_id, "사진을 읽으려면 ANTHROPIC_API_KEY 설정이 필요해요. 지금은 글을 복사해서 보내 주세요.", first["message_id"])
                    continue
                programs = extract_simple(text, now)
            if not programs:
                bot.reply(chat_id, "행사 안내를 찾지 못했어요. 신청 날짜가 보이게 캡처하거나 글을 붙여 주세요.", first["message_id"])
                continue

            lines = []
            for p in programs:
                ev = to_event(p, url, now)
                existing = next((i for i, e in enumerate(inbox) if e["id"] == ev["id"]), None)
                if existing is None:
                    inbox.append(ev)
                    num = len(inbox)
                else:
                    inbox[existing] = ev
                    num = existing + 1
                lines.append(summary(ev, num))
            bot.reply(chat_id, "✅ 저장했어요\n\n" + "\n\n".join(lines) + "\n\n잘못 읽었으면 /del 번호", first["message_id"])
        except Exception as e:  # 한 메시지가 실패해도 나머지는 처리
            print(f"메시지 처리 실패: {e!r}", file=sys.stderr)
            bot.reply(chat_id, f"⚠️ 읽다가 문제가 생겼어요: {html.escape(str(e)[:200])}", first["message_id"])

    save_json(INBOX, inbox)
    save_json(OFFSET, {"offset": updates[-1]["update_id"] + 1})
    print(f"받은 메시지 {len(updates)}건 처리 · 받은편지함 {len(inbox)}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
