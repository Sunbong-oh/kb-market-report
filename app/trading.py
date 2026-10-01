"""매수 제한 규칙 + 모의매매(paper trading) 원장.

KB OpenAPI 공개 샘플(kbsecurities/kb-openapi)에는 트레이딩(주문) TR이 포함되어
있지 않으므로 주문은 로컬 SQLite 모의매매로 체결한다. 체결가는 KB 현재가(IVU10140).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from .config import Settings

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


# ------------------------------------------------------------- 매수 규칙
@dataclass
class RuleResult:
    allowed: bool
    window_active: bool
    window: str
    reason: str


def rule_window(s: Settings, now: datetime) -> tuple[datetime, datetime]:
    h, m = (int(x) for x in s.market_open.split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return start, start + timedelta(minutes=s.rule_window_minutes)


def check_buy_rule(s: Settings, change_pct: float | None, now: datetime | None = None) -> RuleResult:
    """장 시작 후 N분(기본 60분) 이내에 상승 중인 종목은 매수하지 않는다.

    '상승' = 전일 종가 대비 등락률이 RULE_MAX_CHANGE_PCT(기본 0%) 초과.
    제한 시간대에 등락률을 알 수 없으면 안전하게 매수를 막는다.
    """
    now = now or now_kst()
    start, end = rule_window(s, now)
    window = f"{start:%H:%M}~{end:%H:%M}"
    active = now.weekday() < 5 and start <= now < end
    if not active:
        return RuleResult(True, False, window, "매수 제한 시간대가 아닙니다.")
    if change_pct is None:
        return RuleResult(False, True, window, f"장 시작 후 {s.rule_window_minutes}분 이내이며 등락률을 확인할 수 없어 매수를 제한합니다.")
    if change_pct > s.rule_max_change_pct:
        return RuleResult(
            False,
            True,
            window,
            f"장 시작 후 {s.rule_window_minutes}분 이내({window}) 상승 종목(+{change_pct:.2f}%)은 매수할 수 없습니다.",
        )
    return RuleResult(True, True, window, f"제한 시간대이지만 상승 종목이 아니므로({change_pct:+.2f}%) 매수 가능합니다.")


# ------------------------------------------------------------ 호가단위
def tick_size(price: float) -> int:
    """KRX 주식 호가가격단위 (2023년 개편, 코스피·코스닥 공통)."""
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < limit:
            return tick
    return 1_000


def market_open_now(now: datetime | None = None) -> bool:
    now = now or now_kst()
    return now.weekday() < 5 and time(9, 0) <= now.time() < time(15, 30)


# ------------------------------------------------------------ 모의매매
class PaperBroker:
    """모의매매 원장.

    - 시장가: 현재가로 즉시 체결
    - 지정가: 매수는 현재가 ≤ 지정가, 매도는 현재가 ≥ 지정가이면 즉시(현재가로) 체결,
      아니면 '미체결'로 두었다가 가격이 닿으면 체결. 당일 15:30이 지나면 자동 취소.
    - 매수 제한 규칙(장 시작 후 1시간 내 상승 종목)은 주문할 때와 체결할 때 모두 검사.
    """

    def __init__(self, db_path: Path, initial_cash: int):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.initial_cash = initial_cash
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    side TEXT NOT NULL,          -- buy / sell
                    qty INTEGER NOT NULL,
                    price REAL,                  -- 체결가 (미체결이면 NULL)
                    status TEXT NOT NULL,        -- open / filled / rejected / cancelled
                    reason TEXT
                );
                """
            )
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(orders)")}
            if "order_type" not in cols:
                self._conn.execute("ALTER TABLE orders ADD COLUMN order_type TEXT NOT NULL DEFAULT 'market'")
            if "limit_price" not in cols:
                self._conn.execute("ALTER TABLE orders ADD COLUMN limit_price REAL")
            if "filled_at" not in cols:
                self._conn.execute("ALTER TABLE orders ADD COLUMN filled_at TEXT")

    # ------------------------------------------------------------ 조회
    def _filled(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM orders WHERE status='filled' ORDER BY id").fetchall()

    def cash(self) -> float:
        cash = float(self.initial_cash)
        for o in self._filled():
            amt = o["qty"] * o["price"]
            cash += -amt if o["side"] == "buy" else amt
        return cash

    def open_orders(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM orders WHERE status='open' ORDER BY id")]

    def buying_power(self) -> dict:
        reserved = sum(o["qty"] * o["limit_price"] for o in self.open_orders() if o["side"] == "buy")
        cash = self.cash()
        return {"cash": cash, "reserved": reserved, "available": cash - reserved}

    def positions(self) -> dict[str, dict]:
        pos: dict[str, dict] = {}
        for o in self._filled():
            p = pos.setdefault(o["code"], {"code": o["code"], "name": o["name"], "qty": 0, "avg_price": 0.0})
            if o["side"] == "buy":
                total = p["avg_price"] * p["qty"] + o["price"] * o["qty"]
                p["qty"] += o["qty"]
                p["avg_price"] = total / p["qty"]
            else:
                p["qty"] -= o["qty"]
                if p["qty"] == 0:
                    p["avg_price"] = 0.0
        return {k: v for k, v in pos.items() if v["qty"] > 0}

    def orders(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ 기록
    def record(self, code: str, name: str, side: str, qty: int, price: float | None, status: str, reason: str,
               order_type: str = "market", limit_price: float | None = None) -> dict:
        ts = now_kst().strftime("%Y-%m-%d %H:%M:%S")
        filled_at = ts if status == "filled" else None
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO orders (ts, code, name, side, qty, price, status, reason, order_type, limit_price, filled_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, code, name, side, qty, price, status, reason, order_type, limit_price, filled_at),
            )
        return {"id": cur.lastrowid, "ts": ts, "code": code, "name": name, "side": side, "qty": qty, "price": price,
                "status": status, "reason": reason, "order_type": order_type, "limit_price": limit_price, "filled_at": filled_at}

    def _update(self, order_id: int, **fields) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._conn:
            self._conn.execute(f"UPDATE orders SET {sets} WHERE id=?", (*fields.values(), order_id))

    # ------------------------------------------------------------ 주문
    def place(self, s: Settings, quote: dict, side: str, qty: int, limit_price: float | None = None) -> dict:
        """limit_price가 없으면 시장가, 있으면 지정가."""
        with self._lock:
            code, name, price = quote["code"], quote["name"], quote["price"]
            order_type = "limit" if limit_price else "market"
            rec = lambda st, rs, px=None: self.record(code, name, side, qty, px, st, rs, order_type, limit_price)  # noqa: E731
            if not price:
                return rec("rejected", "현재가를 조회할 수 없습니다.")
            if limit_price:
                tick = tick_size(limit_price)
                if limit_price % tick:
                    return rec("rejected", f"호가단위가 맞지 않습니다 ({limit_price:,.0f}원은 {tick:,}원 단위).")
                lo, hi = quote.get("lower_limit"), quote.get("upper_limit")
                if (lo and limit_price < lo) or (hi and limit_price > hi):
                    return rec("rejected", f"가격제한폭({lo:,.0f}~{hi:,.0f}원)을 벗어난 가격입니다.")
            rule = None
            if side == "buy":
                rule = check_buy_rule(s, quote.get("change_pct"))
                if not rule.allowed:
                    return {**rec("rejected", rule.reason), "rule": asdict(rule)}
                need = (limit_price or price) * qty
                if need > self.buying_power()["available"]:
                    return rec("rejected", "주문가능금액이 부족합니다.")
            else:
                held = self.positions().get(code, {}).get("qty", 0)
                pending = sum(o["qty"] for o in self.open_orders() if o["code"] == code and o["side"] == "sell")
                if qty > held - pending:
                    return rec("rejected", f"매도 가능 수량({held - pending}주)을 초과했습니다.")

            marketable = not limit_price or (price <= limit_price if side == "buy" else price >= limit_price)
            if marketable and market_open_now():
                order = rec("filled", "모의 체결 (현재가)" if not limit_price else "모의 체결 (지정가 도달, 현재가 체결)", price)
            elif not limit_price:
                order = rec("rejected", "장 운영시간(09:00~15:30)이 아닙니다.")
            else:
                order = rec("open", "미체결 · 가격 도달 시 체결 (당일 15:30까지)")
            if rule:
                order["rule"] = asdict(rule)
            return order

    def cancel(self, order_id: int, reason: str = "사용자 취소") -> bool:
        with self._lock:
            row = self._conn.execute("SELECT status FROM orders WHERE id=?", (order_id,)).fetchone()
            if not row or row["status"] != "open":
                return False
            self._update(order_id, status="cancelled", reason=reason)
            return True

    def try_fill(self, s: Settings, order: dict, quote: dict) -> dict | None:
        """미체결 지정가 주문 1건을 현재가로 체결 시도. 체결되면 갱신된 주문을 돌려준다."""
        price = quote.get("price")
        if not price or not market_open_now():
            return None
        limit = order["limit_price"]
        if not (price <= limit if order["side"] == "buy" else price >= limit):
            return None
        if order["side"] == "buy" and not check_buy_rule(s, quote.get("change_pct")).allowed:
            return None  # 제한 시간대에 상승 중이면 체결 보류 (제한이 풀리면 다시 시도)
        with self._lock:
            row = self._conn.execute("SELECT status FROM orders WHERE id=?", (order["id"],)).fetchone()
            if not row or row["status"] != "open":
                return None
            ts = now_kst().strftime("%Y-%m-%d %H:%M:%S")
            self._update(order["id"], status="filled", price=price, filled_at=ts, reason="모의 체결 (지정가 도달, 현재가 체결)")
        return {**order, "status": "filled", "price": price, "filled_at": ts}

    def expire_day_orders(self) -> int:
        """장 마감 후 당일 미체결 주문 자동 취소."""
        n = 0
        for o in self.open_orders():
            n += self.cancel(o["id"], "장 마감 자동 취소 (당일 유효 주문)")
        return n
