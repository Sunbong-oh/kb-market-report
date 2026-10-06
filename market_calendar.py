"""증시 캘린더(calendar/YYYY-MM.json)에서 '오늘 저녁 발표'와 '다음 거래일 일정'을 뽑아 리포트 본문에 붙인다.

캘린더 파일은 매월 '증시 캘린더' 아티팩트를 보고 같은 형식으로 calendar/ 폴더에 추가한다.
해당 월 파일이 없으면 이 섹션만 조용히 빠지고 리포트는 그대로 나간다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

CAL_DIR = Path(__file__).resolve().parent / "calendar"
FIELDS = (("earnings_kr", "국내 실적"), ("earnings_us", "미국 실적(한국시간 밤~새벽)"), ("macro", "경제지표·이슈"), ("events", "행사"))
_cache: dict[str, dict] = {}


def _month(d: date) -> dict:
    key = f"{d:%Y-%m}"
    if key not in _cache:
        path = CAL_DIR / f"{key}.json"
        try:
            _cache[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache[key] = {}
    return _cache[key]


def _day(d: date) -> dict:
    return (_month(d).get("days") or {}).get(d.isoformat()) or {}


def _is_holiday(d: date) -> bool:
    return d.weekday() >= 5 or d.isoformat() in (_month(d).get("holidays_kr") or [])


def next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while _is_holiday(nxt):
        nxt += timedelta(days=1)
    return nxt


def _lines(day: dict) -> list[str]:
    out = []
    for key, label in FIELDS:
        if day.get(key):
            out.append(f"- {label}: " + " · ".join(day[key]))
    return out


def build_section(now: datetime) -> list[str]:
    """리포트 본문에 넣을 줄들 (빈 리스트 = 캘린더 자료 없음)."""
    today = now.date()
    tomorrow = next_trading_day(today)
    lines: list[str] = []
    tonight = _lines(_day(today))
    if tonight:
        lines += ["", "■ 오늘 저녁 발표 실적·주요 이슈", *tonight]
    nxt = _lines(_day(tomorrow))
    if nxt or _month(tomorrow):
        label = f"{tomorrow.month}월 {tomorrow.day}일({'월화수목금토일'[tomorrow.weekday()]})"
        if tomorrow != today + timedelta(days=1):
            label += " · 다음 거래일"
        lines += ["", f"■ 내일 일정 · {label}", *(nxt or ["- 주요 일정 없음"])]
    if lines:
        lines += ["(★ 핵심 · 증시 캘린더 기준, 해외 일정은 현지시각이며 변경될 수 있습니다)"]
    return lines
