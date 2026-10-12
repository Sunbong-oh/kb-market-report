"""KB증권 OpenAPI 기반 주식매매 웹사이트 (FastAPI)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import demo, market, themes
from .config import settings
from .flow_upload import backfill, live_loop, upload_loop
from .futures_flow import FuturesFlowStore, record_loop
from .kb_client import KBApiError, KBClient
from .report_schedule import report_loop
from .stock_names import StockResolver
from .telegram_bot import TelegramOrderBot
from .trading import PaperBroker, check_buy_rule, now_kst, rule_window

STATIC = Path(__file__).parent / "static"
CODE_RE = re.compile(r"^[0-9A-Z]{6}$")

kb = KBClient(settings)
broker = PaperBroker(settings.db_path, settings.paper_initial_cash)
flow_store = FuturesFlowStore(settings.db_path.with_name("futures_flow.db"))
resolver = StockResolver(kb, settings.db_path.with_name("stock_names.db"))  # 종목명 ↔ 코드 (웹 검색·텔레그램 공용)
bot: TelegramOrderBot | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global bot
    tasks = []
    if os.environ.get("REPORT_ONLY"):
        # 클라우드(GitHub Actions) 리포트 전용 실행: 화면·API만 제공하고
        # 수급 기록·리포트 예약·미체결 처리·텔레그램 봇(노트북 봇과 충돌)은 켜지 않는다
        yield
        await kb.close()
        return
    if not settings.demo_mode:
        tasks.append(asyncio.create_task(record_loop(kb, flow_store)))
        tasks.append(asyncio.create_task(report_loop()))  # 월~금 15:50 텔레그램 리포트
        tasks.append(asyncio.create_task(upload_loop(lambda: get_futures(1))))  # 15:46 선물 기록 → GitHub (사이트 차트)
        tasks.append(asyncio.create_task(live_loop(lambda: get_futures(1))))  # 장중 1분마다 → flow-live (실시간 차트)
        tasks.append(asyncio.create_task(backfill(flow_store.series)))  # 켤 때 한 번: DB에 남은 지난 날짜(예: 10/8) 업로드
    tasks.append(asyncio.create_task(fill_loop()))  # 지정가 미체결 주문 체결·장마감 취소
    # 텔레그램 봇 주문: .env에 봇 토큰·채팅 ID가 있으면 켠다
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        bot = TelegramOrderBot(token, chat_id, kb, broker, resolver, settings)
        tasks.append(asyncio.create_task(bot.run()))
    yield
    for t in tasks:
        t.cancel()
    await kb.close()


app = FastAPI(title="KB OpenAPI 주식매매", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(KBApiError)
async def kb_error(_: Request, exc: KBApiError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


def _code(code: str) -> str:
    code = code.strip().upper()
    if not CODE_RE.match(code):
        raise HTTPException(400, "종목코드는 6자리입니다. (예: 005930)")
    return code


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
async def status():
    now = now_kst()
    start, end = rule_window(settings, now)
    return {
        "demo_mode": settings.demo_mode,
        "trading_mode": "paper",
        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
        "rule": {
            "window": f"{start:%H:%M}~{end:%H:%M}",
            "active": now.weekday() < 5 and start <= now < end,
            "max_change_pct": settings.rule_max_change_pct,
        },
    }


# ------------------------------------------------------------------ 시세
@app.get("/api/search")
async def search_stock(q: str):
    """종목명(한글)·코드로 종목 찾기 - 검색창 자동완성."""
    return await resolver.search(q)


@app.get("/api/quote/{code}")
async def get_quote(code: str):
    q = await market.quote(kb, _code(code))
    q["buy_rule"] = asdict(check_buy_rule(settings, q["change_pct"]))
    resolver.learn(q["code"], q["name"])
    return q


@app.get("/api/program/stock/{code}")
async def get_program_stock(code: str):
    return await market.program_by_stock(kb, _code(code))


# ------------------------------------------------------------ 시장 동향
@app.get("/api/market")
async def get_market():
    """시장종합: 지수·프로그램매매(차익/비차익)·투자자별 순매수."""
    return await market.market_summary(kb)


@app.get("/api/macro")
async def get_macro():
    """원/달러 환율 + 국고채 10년물 금리."""
    usd, bond = await asyncio.gather(market.usd_krw(kb), market.treasury_10y(kb))
    return {"usd_krw": usd, "treasury_10y": bond}


@app.get("/api/indices/intraday")
async def get_indices_intraday():
    """시장 지수 미니차트용 당일 흐름 - 코스피·코스닥 IVS11560 지수 1분봉.

    나스닥 선물·KOSPI200 선물은 수치만 표시한다 (분 단위 기록이 필요한 차트는 그리지 않음).
    """
    charts = await asyncio.gather(*(market.index_minute(kb, i) for i in market.INDEX_CHART_MARKET))
    return dict(zip(market.INDEX_CHART_MARKET, charts))


@app.get("/api/themes")
async def get_themes():
    """오늘의 강세 테마 (네이버 금융 테마 등락률 상위, 실패 시 KB 업종랭킹)."""
    return await themes.top_themes(kb)


@app.get("/api/futures")
async def get_futures(minutes: int = 1):
    """KOSPI200 최근월 선물 분봉 + 투자자별 선물 순매수 분 단위 추이."""
    if minutes not in (1, 3, 5):
        raise HTTPException(400, "minutes는 1, 3, 5 중 하나입니다.")
    summary = await kb.call("IVSA0070")
    fut = market.front_futures(summary)
    if not fut:
        raise HTTPException(502, "IVSA0070 응답에서 KOSPI200 선물을 찾지 못했습니다.")
    bars = await market.futures_minute(kb, fut["code"], minutes)
    if settings.demo_mode:
        dt, flows = now_kst().strftime("%Y%m%d"), demo.futures_flows([b["t"] for b in bars])
    else:
        dt, flows = flow_store.series()
    return {
        "futures": fut,
        "bars": bars,
        "flows": flows,
        "flow_date": dt,
        "current_flows": market.investor_futures(summary),
        "recording": not settings.demo_mode,
    }


# -------------------------------------------------------------- 모의매매
class OrderIn(BaseModel):
    code: str
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0, le=1_000_000)
    order_type: Literal["limit", "market"] = "limit"
    price: int | None = Field(default=None, gt=0)  # 지정가 (order_type=limit일 때 필수)


@app.post("/api/orders")
async def place_order(order: OrderIn):
    if order.order_type == "limit" and not order.price:
        raise HTTPException(400, "지정가 주문은 가격을 입력해야 합니다.")
    q = await market.quote(kb, _code(order.code))
    return broker.place(settings, q, order.side, order.qty, order.price if order.order_type == "limit" else None)


@app.post("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: int):
    if not broker.cancel(order_id):
        raise HTTPException(400, "취소할 수 없는 주문입니다 (이미 체결·취소됨).")
    return {"ok": True}


@app.get("/api/orders")
async def list_orders():
    return broker.orders()


@app.get("/api/buying-power")
async def buying_power():
    """주문가능금액 = 예수금 - 미체결 매수 주문 금액, 종목별 보유수량."""
    return {**broker.buying_power(), "positions": broker.positions()}


async def fill_loop() -> None:
    """미체결 지정가 주문: 장중 5초마다 현재가를 확인해 가격이 닿으면 체결, 15:30 이후 자동 취소."""
    while True:
        try:
            now = now_kst()
            opens = broker.open_orders()
            if opens and now.weekday() < 5 and now.strftime("%H:%M") >= "15:30":
                if broker.expire_day_orders() and bot:
                    await bot.notify_expired(opens)
            elif opens:
                for code in {o["code"] for o in opens}:
                    q = await market.quote(kb, code)
                    for o in (o for o in opens if o["code"] == code):
                        filled = broker.try_fill(settings, o, q)
                        if filled and bot:
                            await bot.notify_fill(filled)  # 텔레그램으로 체결 알림
        except Exception as exc:  # 시세 조회 실패 등은 다음 주기에 재시도
            logging.getLogger("fill_loop").warning("미체결 처리 실패: %s", exc)
        await asyncio.sleep(5)
