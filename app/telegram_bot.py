"""텔레그램 봇 주문 (텍스트·음성입력).

폰 키보드의 🎤 음성 입력으로 "삼성전자 10주 매수"처럼 보내면
  1) 종목·수량·매수/매도를 알아듣고
  2) 현재가·예상금액·매수 규칙 결과와 함께 [확인]/[취소] 버튼을 보낸다
  3) [확인]을 눌러야만 모의매매로 체결한다 (음성 인식 오류 대비)

서버가 텔레그램에 먼저 접속하는 long polling 방식이라 서버를 외부에 공개할 필요가 없다.
.env의 TELEGRAM_CHAT_ID(내 채팅)에서 온 메시지만 처리하고, 2분 넘은 메시지·버튼은 무시한다.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import time
from dataclasses import dataclass

import httpx

from . import market
from .config import Settings
from .kb_client import KBClient
from .stock_names import StockResolver
from .trading import PaperBroker, check_buy_rule, tick_size

log = logging.getLogger("telegram_bot")
STALE_SEC = 120

HELP = (
    "📈 KB 모의매매 봇\n\n"
    "말로 대화하듯 (키보드 🎤 · Android Auto · CarPlay 음성 답장)\n"
    "  • 삼성전자 지금 얼마야?\n"
    "  • 10주 사줘  ← 방금 물어본 종목\n"
    "  • 확인 / 취소  ← 버튼 대신 말로\n"
    "주문 (기본 지정가)\n"
    "  • 삼성전자 10주 27만 원에 매수\n"
    "  • SK하이닉스 3주 180만 5천 원에 팔아줘\n"
    "  • 카카오 5주 매수  ← 가격 없으면 현재가로 지정가\n"
    "  • 삼성전자 10주 시장가 매수\n"
    "미체결: 미체결 (취소 버튼)   내역: 주문내역\n"
    "웹 매매창: 매매창 (또는 입력칸 옆 [매매창] 버튼)\n\n"
    "주문은 [확인] 버튼을 눌러야 체결됩니다. 모의매매이며 실제 계좌와 연결되지 않습니다.\n"
    "장 시작 후 1시간 안에 상승한 종목은 매수할 수 없습니다."
)

BUY_WORDS = ("매수", "사줘", "사 줘", "사주", "살래", "사고", "구매", "buy")
SELL_WORDS = ("매도", "팔아", "팔래", "팔고", "처분", "sell")
KOR_NUM = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4, "다섯": 5, "여섯": 6,
           "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "스무": 20, "서른": 30, "쉰": 50, "백": 100}
FILLER = r"(시장가로?|시장가|주문|좀|해\s*줘|해주세요|해|줘|요|을|를|으로|로|개|주)$"


@dataclass
class Pending:
    code: str
    name: str
    side: str
    qty: int
    created: float
    limit_price: float | None = None  # 지정가 (None이면 시장가)
    market: bool = False              # 말로 '시장가'를 요청했는지 (종목 후보 선택 뒤에도 유지)
    ask_price: float | None = None    # 말한 가격 (없으면 현재가로 지정가)


PRICE_WON = re.compile(r"((?:\d[\d,]*\s*억\s*)?(?:\d[\d,]*\s*만\s*)?(?:\d[\d,]*\s*천\s*)?(?:\d[\d,]*)?)\s*원\s*(?:에|으로|로|에서|짜리)?")
PRICE_MAN = re.compile(r"(\d[\d,]*\s*만(?:\s*\d[\d,]*\s*천?)?)\s*(?:에|으로|로)")  # '27만에'처럼 '원' 생략
MARKET_WORDS = re.compile(r"시장가로?|현재가로|지금\s*가격으?로")


def kr_amount(expr: str) -> int:
    """'27만 1,500' → 271500, '27만5천' → 275000, '271,500' → 271500."""
    mult = {"억": 100_000_000, "만": 10_000, "천": 1_000, "": 1}
    return sum(int(n) * mult[u] for n, u in re.findall(r"(\d+)\s*(억|만|천)?", expr.replace(",", "")))


def parse_price(msg: str) -> tuple[int | None, bool, str]:
    """(지정가, 시장가 요청 여부, 가격 표현을 뺀 문장) - '삼성전자 10주 27만 원에 매수' → (270000, False, '삼성전자 10주 매수')."""
    market = bool(MARKET_WORDS.search(msg))
    s = MARKET_WORDS.sub(" ", msg)
    for rx in (PRICE_WON, PRICE_MAN):
        for m in rx.finditer(s):
            expr = m.group(1).strip()
            if re.search(r"\d", expr):
                return kr_amount(expr), market, (s[: m.start()] + " " + s[m.end():]).strip()
    return None, market, s


def parse_order(msg: str) -> tuple[str | None, int | None, str]:
    """(side, qty, 종목 문자열) - 예: '삼성전자 10주 매수' → ('buy', 10, '삼성전자')."""
    s = msg.strip()
    low = s.lower()
    side = "sell" if any(w in low for w in SELL_WORDS) else "buy" if any(w in low for w in BUY_WORDS) else None
    for w in (*BUY_WORDS, *SELL_WORDS):
        s = re.sub(re.escape(w), " ", s, flags=re.I)
    qty = None
    # '10주'처럼 단위가 붙은 숫자 우선, 없으면 6자리 종목코드가 아닌 단독 숫자
    m = re.search(r"(\d[\d,]*)\s*(주|개)", s) or re.search(r"(?<![0-9A-Za-z])(\d{1,5})(?![0-9A-Za-z])", s)
    if m:
        qty = int(m.group(1).replace(",", ""))
        s = s[: m.start()] + " " + s[m.end():]
    else:
        m = re.search(r"(" + "|".join(sorted(KOR_NUM, key=len, reverse=True)) + r")\s*(주|개)", s)
        if m:
            qty = KOR_NUM[m.group(1)]
            s = s[: m.start()] + " " + s[m.end():]
    words = [w for w in s.split() if not re.fullmatch(FILLER, w)]
    words = [re.sub(r"(을|를|주|개)$", "", w) if len(w) > 2 else w for w in words]
    return side, qty, " ".join(words).strip()


def won(v: float | None) -> str:
    return "-" if v is None else f"{v:,.0f}"


class TelegramOrderBot:
    def __init__(self, token: str, chat_id: str, kb: KBClient, broker: PaperBroker, resolver: StockResolver, settings: Settings):
        self.api = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id)
        self.kb, self.broker, self.resolver, self.s = kb, broker, resolver, settings
        self.web_url = os.environ.get("TRADING_WEB_URL", "").rstrip("/")
        self.pending: dict[str, Pending] = {}
        self.last_stock: tuple[str, str, float] | None = None
        self._ids = itertools.count(1)
        self.http = httpx.AsyncClient(timeout=70)

    # --------------------------------------------------------- telegram
    async def call(self, method: str, **params) -> dict:
        data = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in params.items()}
        res = await self.http.post(f"{self.api}/{method}", data=data)
        body = res.json()
        if not body.get("ok"):
            log.warning("telegram %s 실패: %s", method, body)
        return body

    async def send(self, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> None:
        params = {"chat_id": self.chat_id, "text": text}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}
        await self.call("sendMessage", **params)

    async def setup_menu(self) -> None:
        """입력칸 옆 [매매창] 메뉴 버튼 - 텔레그램 안에서 매매 웹사이트(Tailscale https 주소)를 연다."""
        if self.web_url:
            await self.call("setChatMenuButton", chat_id=self.chat_id,
                            menu_button={"type": "web_app", "text": "매매창", "web_app": {"url": self.web_url}})

    async def run(self) -> None:
        try:
            await self.setup_menu()
        except Exception as exc:
            log.warning("메뉴 버튼 설정 실패: %s", exc)
        offset = None
        while True:
            try:
                params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
                if offset:
                    params["offset"] = offset
                body = await self.call("getUpdates", **params)
                for upd in body.get("result", []):
                    offset = upd["update_id"] + 1
                    try:
                        await self.handle(upd)
                    except Exception as exc:
                        log.exception("업데이트 처리 실패")
                        await self.send(f"⚠️ 처리 중 오류: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("텔레그램 폴링 오류: %s", exc)
                await asyncio.sleep(5)

    # ----------------------------------------------------------- handle
    async def handle(self, upd: dict) -> None:
        if "callback_query" in upd:
            cq = upd["callback_query"]
            if str(cq.get("message", {}).get("chat", {}).get("id")) != self.chat_id:
                return
            await self.call("answerCallbackQuery", callback_query_id=cq["id"])
            await self.call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=cq["message"]["message_id"],
                            reply_markup={"inline_keyboard": []})
            await self.on_button(cq["data"])
            return
        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != self.chat_id:
            return  # 내 채팅이 아니면 무시
        if time.time() - msg.get("date", 0) > STALE_SEC:
            return  # 서버가 꺼져 있던 동안 쌓인 오래된 명령은 실행하지 않음
        if "voice" in msg or "audio" in msg:
            await self.send("음성 녹음 파일은 알아듣지 못해요. 키보드 마이크나 차량 음성 답장으로 말씀해 주세요. "
                            "예: 삼성전자 지금 얼마야?")
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        key = re.sub(r"[\s.!?~,]", "", text).lower()
        if text in ("/start", "/help", "도움말", "help"):
            await self.send(HELP)
        elif key in CONFIRM_WORDS or key in CANCEL_WORDS:
            await self.on_voice_answer(key in CONFIRM_WORDS)
        elif key in ("매매창", "/web", "사이트", "웹"):
            await self.send_web_link()
        elif "미체결" in text:
            await self.send_open_orders()
        elif "내역" in text:
            await self.send_history()
        elif is_quote_question(text):
            await self.send_quote(quote_target(text))
        else:
            await self.on_order_text(text)

    async def send_web_link(self) -> None:
        if not self.web_url:
            await self.send("매매창 주소가 아직 설정되지 않았어요 (.env의 TRADING_WEB_URL).")
            return
        await self.call("sendMessage", chat_id=self.chat_id,
                        text=f"🖥 매매창\n{self.web_url}\n\n폰의 Tailscale 연결이 켜져 있어야 열립니다.",
                        reply_markup={"inline_keyboard": [[{"text": "텔레그램에서 열기", "web_app": {"url": self.web_url}}],
                                                          [{"text": "브라우저로 열기", "url": self.web_url}]]})

    async def send_history(self) -> None:
        rows = self.broker.orders(10)
        if not rows:
            await self.send("주문 내역이 없습니다.")
            return
        icon = {"filled": "✅", "open": "⏳", "rejected": "⛔", "cancelled": "✖️"}
        lines = [f"{icon.get(o['status'], '•')} {o['ts'][5:16]} {o['name']} {o['qty']}주 "
                 f"{'지정' if o.get('order_type') == 'limit' else '시장'} {'매수' if o['side'] == 'buy' else '매도'}"
                 + (f" 지정 {won(o['limit_price'])}" if o.get("limit_price") else "")
                 + (f" → 체결 {won(o['price'])}" if o["status"] == "filled" else f"\n   └ {o['reason']}") for o in rows]
        await self.send("🧾 최근 주문 (모의)\n" + "\n".join(lines))

    async def send_open_orders(self) -> None:
        opens = self.broker.open_orders()
        if not opens:
            await self.send("미체결 주문이 없어요.")
            return
        lines = [f"• {o['name']} {o['qty']}주 지정가 {won_say(o['limit_price'])} {'매수' if o['side'] == 'buy' else '매도'}" for o in opens]
        buttons = [[(f"취소: {o['name']} {o['qty']}주 {won(o['limit_price'])}", f"cx:{o['id']}")] for o in opens]
        await self.send(f"미체결 주문 {len(opens)}건이에요.\n" + "\n".join(lines), buttons)

    async def notify_fill(self, o: dict) -> None:
        """지정가 미체결 주문이 나중에 체결되면 알림 (서버의 fill_loop가 호출)."""
        label = "매수" if o["side"] == "buy" else "매도"
        await self.send(f"🔔 {o['name']} {o['qty']}주 {label} 체결됐어요. 체결가 {won_say(o['price'])} "
                        f"(지정가 {won_say(o['limit_price'])}).\n\n[모의] 금액 {won(o['price'] * o['qty'])}원")

    async def notify_expired(self, opens: list[dict]) -> None:
        names = ", ".join(f"{o['name']} {o['qty']}주" for o in opens)
        await self.send(f"장 마감으로 미체결 주문 {len(opens)}건이 자동 취소됐어요: {names}")

    # ------------------------------------------------------- 종목 찾기
    def recent_stock(self) -> tuple[str, str] | None:
        """방금 물어본(또는 주문한) 종목 - '10주 사줘'처럼 종목을 생략하면 이걸 쓴다 (5분 유효)."""
        if self.last_stock and time.time() - self.last_stock[2] < CONTEXT_SEC:
            return self.last_stock[0], self.last_stock[1]
        return None

    def remember(self, code: str, name: str) -> None:
        self.resolver.learn(code, name)
        self.last_stock = (code, name, time.time())

    async def pick_stock(self, query: str, side: str | None, qty: int | None,
                         price: int | None = None, market_order: bool = False) -> tuple[str, str] | None:
        if not query or re.fullmatch(r"(그거|그것|이거|이것|그\s*종목|이\s*종목|방금\s*거?|아까\s*거?)", query):
            recent = self.recent_stock()
            if recent:
                return recent
            await self.send("어떤 종목인지 말씀해 주세요. 예: 삼성전자 10주 매수")
            return None
        cands = await self.resolver.resolve(query)
        if not cands:
            await self.send(f"'{query}' 종목을 찾지 못했어요. 종목 이름을 다시 말씀해 주세요.")
            return None
        if len(cands) > 1:
            names = ", ".join(n for _, n in cands)
            buttons = None
            if side and qty:
                pid = self._new_pending(Pending("", "", side, qty, time.time(), market=market_order, ask_price=price))
                buttons = [[(f"{n} ({c})", f"pick:{pid}:{c}")] for c, n in cands] + [[("취소", f"no:{pid}")]]
            await self.send(f"'{query}' 종목이 여러 개예요: {names}. 정확한 이름으로 다시 말씀해 주세요.", buttons)
            return None
        return cands[0]

    # ------------------------------------------------------------ 시세
    async def send_quote(self, query: str) -> None:
        found = await self.pick_stock(query, None, None)
        if not found:
            return
        q = await market.quote(self.kb, found[0])
        self.remember(q["code"], q["name"])
        rule = check_buy_rule(self.s, q["change_pct"])
        # 차량(Android Auto·CarPlay)이 읽어주기 좋게 첫 문장은 말하듯이, 금액은 '만' 단위로
        spoken = (f"{q['name']} 지금 {won_say(q['price'])}, {change_say(q['change_pct'])}."
                  + ("" if rule.allowed else " 지금은 매수 제한 시간이라 매수할 수 없어요."))
        stats = " · ".join(f"{k} {won(q[f])}" for k, f in (("고가", "high"), ("저가", "low"), ("거래량", "volume")) if q.get(f))
        detail = (f"{stats}\n" if stats else "") + "주문하려면 \"10주 매수\"처럼 말씀하세요."
        await self.send(f"{spoken}\n\n{detail}")

    # ------------------------------------------------------------ 주문
    def _new_pending(self, p: Pending) -> str:
        # 확인 대기 주문은 항상 1개만: 새 주문이 들어오면 이전 대기 주문은 무효
        # (운전 중 '확인'이 잘못 들려도 잊고 있던 옛 주문이 체결되지 않도록)
        pid = str(next(self._ids))
        self.pending = {pid: p}
        return pid

    async def on_order_text(self, text: str) -> None:
        price, market_order, rest = parse_price(text)  # '27만 원에' 같은 가격 표현을 먼저 떼어 냄
        side, qty, query = parse_order(rest)
        if not side and not qty:
            await self.send(f"'{text}'를 알아듣지 못했어요. 예: 삼성전자 지금 얼마야? / 삼성전자 10주 27만 원에 매수")
            return
        if not side or not qty:
            await self.send("몇 주를 매수 또는 매도할지 함께 말씀해 주세요. 예: 10주 27만 원에 매수")
            return
        found = await self.pick_stock(query, side, qty, price, market_order)
        if found:
            await self.confirm(found[0], side, qty, price, market_order)

    async def confirm(self, code: str, side: str, qty: int, price: int | None = None, market_order: bool = False) -> None:
        q = await market.quote(self.kb, code)
        self.remember(q["code"], q["name"])
        label = "매수" if side == "buy" else "매도"
        now_price = q["price"] or 0
        if side == "buy":
            rule = check_buy_rule(self.s, q["change_pct"])
            if not rule.allowed:
                await self.send(f"{q['name']} 매수는 지금 할 수 없어요. {rule.reason}")
                return
        if market_order:
            limit, how = None, "시장가"
        else:
            # 가격을 말하지 않으면 현재가로 지정가 (현재가는 항상 호가단위에 맞음)
            limit = price or now_price
            tick = tick_size(limit)
            if limit % tick:
                down, up = limit // tick * tick, (limit // tick + 1) * tick
                await self.send(f"{won_say(limit)}은 호가단위({tick:,}원)에 맞지 않아요. "
                                f"{won_say(down)}이나 {won_say(up)}으로 다시 말씀해 주세요.")
                return
            lo, hi = q.get("lower_limit"), q.get("upper_limit")
            if (lo and limit < lo) or (hi and limit > hi):
                await self.send(f"{won_say(limit)}은 오늘 가격 범위({won_say(lo)}~{won_say(hi)})를 벗어났어요.")
                return
            how = f"지정가 {won_say(limit)}" + ("" if price else "(현재가)")
        total = (limit or now_price) * qty
        if market_order or (now_price <= limit if side == "buy" else now_price >= limit):
            when = "지금 가격이면 바로 체결돼요."
        else:
            when = f"지금은 {won_say(now_price)}이라 미체결로 기다렸다가 가격이 닿으면 체결돼요."
        pid = self._new_pending(Pending(q["code"], q["name"], side, qty, time.time(), limit_price=limit))
        await self.send(
            f"{q['name']} {qty}주 {how} {label}, 약 {won_say(total, approx=True)}이에요. {when} "
            "주문하려면 '확인', 그만두려면 '취소'라고 말씀하세요.\n\n"
            f"[모의] 현재가 {won(now_price)}원 · 2분 안에 답해 주세요",
            [[(f"✅ {label} 확인", f"ok:{pid}"), ("❌ 취소", f"no:{pid}")]],
        )

    async def on_voice_answer(self, yes: bool) -> None:
        """'확인'/'취소'를 말로 답하면 가장 최근의 확인 대기 주문을 처리 (핸즈프리용)."""
        now = time.time()
        live = [(k, v) for k, v in self.pending.items() if v.code and now - v.created < STALE_SEC]
        if not live:
            await self.send("확인을 기다리는 주문이 없어요.")
            return
        _, p = max(live, key=lambda kv: kv[1].created)
        self.pending = {}
        if yes:
            await self.execute(p)
        else:
            await self.send("주문을 취소했어요.")

    async def on_button(self, data: str) -> None:
        kind, pid, *rest = data.split(":")
        if kind == "cx":  # 미체결 주문 취소 (pid = 주문번호)
            ok = self.broker.cancel(int(pid))
            await self.send("미체결 주문을 취소했어요." if ok else "이미 체결됐거나 취소된 주문이에요.")
            return
        p = self.pending.pop(pid, None)
        if not p or time.time() - p.created > STALE_SEC:
            await self.send("시간이 지났거나 새 주문으로 바뀐 주문이에요. 다시 말씀해 주세요.")
            return
        self.pending = {}
        if kind == "no":
            await self.send("주문을 취소했어요.")
        elif kind == "pick":
            await self.confirm(rest[0], p.side, p.qty, p.ask_price, p.market)
        elif kind == "ok":
            await self.execute(p)

    async def execute(self, p: Pending) -> None:
        q = await market.quote(self.kb, p.code)  # 체결 직전 현재가·규칙 다시 확인
        o = self.broker.place(self.s, q, p.side, p.qty, p.limit_price)
        label = "매수" if p.side == "buy" else "매도"
        if o["status"] == "filled":
            await self.send(f"{o['name']} {o['qty']}주 {label} 체결됐어요. 체결가 {won_say(o['price'])}.\n\n"
                            f"[모의] 금액 {won(o['price'] * o['qty'])}원")
        elif o["status"] == "open":
            await self.send(f"{o['name']} {o['qty']}주 지정가 {won_say(o['limit_price'])} {label} 주문이 접수됐어요. "
                            "가격이 닿으면 체결하고 알려드릴게요. 오늘 15시 30분까지 유효해요.\n\n"
                            "취소하려면 '미체결'이라고 보내세요.")
        else:
            await self.send(f"주문이 거절됐어요. {o['reason']}")


# -------------------------------------------------------- 음성 대화용 도우미
CONTEXT_SEC = 300
CONFIRM_WORDS = {"확인", "네", "예", "응", "좋아", "그래", "주문해", "주문해줘", "진행", "진행해", "오케이", "ok", "ㅇㅇ", "확인해", "확인해줘"}
CANCEL_WORDS = {"취소", "아니", "아니요", "아니오", "하지마", "그만", "취소해", "취소해줘", "안해", "됐어"}
QUOTE_WORDS = ("얼마", "주가", "현재가", "시세", "가격", "어때", "몇원", "몇 원")


def is_quote_question(text: str) -> bool:
    low = text.lower()
    has_side = any(w in low for w in (*BUY_WORDS, *SELL_WORDS))
    return not has_side and (any(w in text for w in QUOTE_WORDS) or text.startswith("/price"))


def quote_target(text: str) -> str:
    """'삼성전자 지금 얼마야?' → '삼성전자'"""
    s = re.sub(r"^(/price|현재가|시세)\s*", "", text)
    s = re.sub(r"[?!.~]", " ", s)
    for w in ("얼마야", "얼마예요", "얼마에요", "얼마니", "얼마", "주가", "현재가", "시세", "가격", "어때", "몇원", "몇 원",
              "지금", "현재", "오늘", "알려줘", "알려", "좀"):
        s = s.replace(w, " ")
    words = [re.sub(r"(은|는|이|가|의|요)$", "", w) if len(w) > 2 else w for w in s.split()]
    return " ".join(w for w in words if w not in ("요", "야")).strip()


def won_say(v: float | None, approx: bool = False) -> str:
    """읽기 좋은 금액: 271000 → '27만 1,000원', approx=True면 만 단위 반올림 → '272만 원'."""
    if v is None:
        return "알 수 없음"
    v = int(round(v))
    if approx and v >= 10_000:
        v = round(v, -4)
    eok, rest = divmod(v, 100_000_000)
    man, rest = divmod(rest, 10_000)
    parts = ([f"{eok:,}억"] if eok else []) + ([f"{man:,}만"] if man else []) + ([f"{rest:,}"] if rest else [])
    if not parts:
        return "0원"
    return " ".join(parts) + ("원" if rest else " 원")


def change_say(pct: float | None) -> str:
    if pct is None:
        return "등락률은 알 수 없어요"
    if pct > 0:
        return f"어제보다 {pct:.2f}% 올랐어요"
    if pct < 0:
        return f"어제보다 {abs(pct):.2f}% 내렸어요"
    return "어제와 같아요"
