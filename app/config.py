"""환경설정 로딩 (.env 또는 환경변수)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "y", "on")


@dataclass(frozen=True)
class Settings:
    base_url: str
    app_key: str
    app_secret: str
    demo_mode: bool
    cache_ttl: float
    # 매수 제한 규칙: 장 시작 후 N분 이내에 상승 중인 종목은 매수하지 않는다
    market_open: str
    rule_window_minutes: int
    rule_max_change_pct: float
    paper_initial_cash: int
    db_path: Path


def load_settings() -> Settings:
    app_key = os.environ.get("KB_OPENAPI_APP_KEY", "")
    app_secret = os.environ.get("KB_OPENAPI_APP_SECRET", "")
    # 키가 없으면 자동으로 데모(가짜 데이터) 모드로 동작한다.
    demo = _bool("DEMO_MODE", not (app_key and app_secret))
    root = Path(__file__).resolve().parent.parent
    return Settings(
        base_url=os.environ.get("KB_OPENAPI_BASE_URL", "https://developer.kbsec.com:32484").rstrip("/"),
        app_key=app_key,
        app_secret=app_secret,
        demo_mode=demo,
        cache_ttl=float(os.environ.get("KB_CACHE_TTL", "10")),
        market_open=os.environ.get("MARKET_OPEN", "09:00"),
        rule_window_minutes=int(os.environ.get("RULE_WINDOW_MINUTES", "60")),
        rule_max_change_pct=float(os.environ.get("RULE_MAX_CHANGE_PCT", "0")),
        paper_initial_cash=int(os.environ.get("PAPER_INITIAL_CASH", "10000000")),
        db_path=root / "data" / "paper_trading.db",
    )


settings = load_settings()
