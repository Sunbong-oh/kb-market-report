"""투자자별 선물 순매수 분 단위 기록.

KB OpenAPI 투자정보 TR에는 투자자별 선물 순매수의 '분 단위 추이'를 주는 TR이 없다
(IVU10430 종목별투자자는 일별만 지원). IVSA0070(시장종합)은 당일 누적값만 주므로
서버가 장중(08:45~15:45) 1분마다 IVSA0070을 호출해 투자자별 누적값을 SQLite에
쌓고, 이것으로 HTS '투자자별매매동향-추이' 같은 분 단위 차트를 그린다.
서버가 꺼져 있던 시간대는 기록되지 않는다. 금액 단위는 억원(HTS 0783 화면과 일치).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from pathlib import Path

from . import market
from .kb_client import KBClient
from .trading import now_kst

log = logging.getLogger("futures_flow")
SESSION = ("0845", "1545")


class FuturesFlowStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS futures_flow_v2 (
                    dt TEXT NOT NULL,        -- YYYYMMDD
                    tm TEXT NOT NULL,        -- HHMM
                    investor TEXT NOT NULL,  -- 외국인, 기관계, 개인, 금융투자, 투신, 은행 …
                    value REAL,              -- 당일 누적 선물 순매수 (억원)
                    PRIMARY KEY (dt, tm, investor)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS index_snap (
                    dt TEXT NOT NULL,        -- YYYYMMDD (KST)
                    tm TEXT NOT NULL,        -- HHMM (KST)
                    symbol TEXT NOT NULL,    -- 예: CME@NQ
                    value REAL,
                    PRIMARY KEY (dt, tm, symbol)
                )
                """
            )
            self._migrate_v1()

    def _migrate_v1(self) -> None:
        """이전 버전(외국인/기관계/개인 3개 컬럼) 기록을 옮긴다."""
        old = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='futures_flow'").fetchone()
        if not old:
            return
        for col, name in (("foreign_nb", "외국인"), ("institution_nb", "기관계"), ("individual_nb", "개인")):
            self._conn.execute(
                f"INSERT OR IGNORE INTO futures_flow_v2 SELECT dt, tm, ?, {col} FROM futures_flow WHERE {col} IS NOT NULL",
                (name,),
            )
        self._conn.execute("DROP TABLE futures_flow")

    def save(self, dt: str, tm: str, flows: dict[str, float | None]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO futures_flow_v2 VALUES (?,?,?,?)",
                [(dt, tm, name, value) for name, value in flows.items() if value is not None],
            )

    def series(self, dt: str | None = None) -> tuple[str, list[dict]]:
        """해당 일자(기본: 기록이 있는 가장 최근 일자)의 분 단위 기록 [{t, 외국인, 기관계, …}]."""
        if dt is None:
            row = self._conn.execute("SELECT MAX(dt) FROM futures_flow_v2").fetchone()
            dt = row[0] if row and row[0] else now_kst().strftime("%Y%m%d")
        rows = self._conn.execute(
            "SELECT tm, investor, value FROM futures_flow_v2 WHERE dt=? ORDER BY tm", (dt,)
        ).fetchall()
        by_tm: dict[str, dict] = {}
        for tm, investor, value in rows:
            by_tm.setdefault(tm, {"t": tm})[investor] = value
        return dt, list(by_tm.values())


    def save_index(self, dt: str, tm: str, symbol: str, value: float | None) -> None:
        if value is None:
            return
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO index_snap VALUES (?,?,?,?)", (dt, tm, symbol, value))

    def index_series(self, symbol: str, dt: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT tm, value FROM index_snap WHERE dt=? AND symbol=? ORDER BY tm", (dt, symbol)
        ).fetchall()
        return [{"t": tm, "c": value} for tm, value in rows]


async def record_loop(kb: KBClient, store: FuturesFlowStore) -> None:
    """1분마다 IVSA0070을 호출해
    - 평일 장중(08:45~15:45): 투자자별 선물 순매수 누적값
    - 항상: 해외지수(나스닥 선물 등, 분봉 TR이 없음) 현재값
    을 기록한다."""
    while True:
        now = now_kst()
        dt, tm = now.strftime("%Y%m%d"), now.strftime("%H%M")
        try:
            summary = await kb.call("IVSA0070")
            if now.weekday() < 5 and SESSION[0] <= tm <= SESSION[1]:
                flows = market.investor_futures(summary)
                if flows:
                    store.save(dt, tm, flows)
            for symbol, value in market.overseas_values(summary).items():
                store.save_index(dt, tm, symbol, value)
        except Exception as exc:  # 네트워크 오류 등은 다음 분에 재시도
            log.warning("IVSA0070 기록 실패: %s", exc)
        await asyncio.sleep(60 - now_kst().second + 1)
