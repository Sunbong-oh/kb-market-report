"""증시 캘린더(calendar/YYYY-MM.json)에서 '오늘 저녁 발표'와 '다음 거래일 일정'을 뽑아 리포트 본문에 붙인다.

캘린더 파일은 매월 '증시 캘린더' 아티팩트를 보고 같은 형식으로 calendar/ 폴더에 추가한다.
해당 월 파일이 없으면 이 섹션만 조용히 빠지고 리포트는 그대로 나간다.
"""

from __future__ import annotations

import html
import json
from datetime import date, datetime, timedelta
from pathlib import Path

CAL_DIR = Path(__file__).resolve().parent / "calendar"
FIELDS = (("earnings_kr", "국내 실적"), ("earnings_us", "미국 실적(한국시간 밤~새벽)"), ("macro", "경제지표·이슈"), ("events", "행사"))
_cache: dict[str, dict] = {}
NAVER_BASIC = "https://m.stock.naver.com/api/stock/{code}/basic"
NAVER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                 "Referer": "https://m.stock.naver.com/"}


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


def _eok(v: float) -> str:
    """억원 -> '119조 3,000억' 식 표기."""
    jo, eok = divmod(round(abs(v)), 10_000)
    text = (f"{jo:,}조" + (f" {eok:,}억" if eok else "")) if jo else f"{eok:,}억"
    return ("-" if v < 0 else "") + text


def _price_change(code: str) -> tuple[float | None, float | None]:
    """네이버 증권에서 (현재가/종가, 등락률%). 실패하면 (None, None)."""
    try:
        import httpx

        r = httpx.get(NAVER_BASIC.format(code=code), headers=NAVER_HEADERS, timeout=10, follow_redirects=True)
        r.raise_for_status()
        d = r.json()
        price = float(str(d.get("closePrice")).replace(",", ""))
        pct = float(str(d.get("fluctuationsRatio")).replace(",", ""))
        if pct > 0 and (d.get("compareToPreviousPrice") or {}).get("name") in ("FALLING", "LOWER_LIMIT"):
            pct = -pct
        return price, pct
    except Exception:
        return None, None


def earnings_results(now: datetime) -> list[dict]:
    """오늘 발표된 국내 실적: 캘린더의 earnings_results(영업이익 예상치·실제치, 억원)에 당일 주가 등락률을 붙인다.

    항목 예: {"name": "삼성전자", "code": "005930", "consensus_op": 1190000, "actual_op": 1250000, "note": "잠정, 매출 210조"}
    actual_op 이 비어 있으면(아직 입력 전) '실제치 미입력'으로 표시해 누락을 눈에 띄게 한다.
    """
    out = []
    for e in _day(now.date()).get("earnings_results") or []:
        cons, act = e.get("consensus_op"), e.get("actual_op")
        price, pct = _price_change(e["code"]) if e.get("code") else (None, None)
        out.append({
            "name": e.get("name", ""), "note": e.get("note", ""), "price": price, "pct": pct,
            "consensus": cons, "actual": act,
            "surprise": (act / cons - 1) * 100 if cons and act is not None else None,
        })
    return out


def _result_lines(rows: list[dict]) -> list[str]:
    lines = []
    for r in rows:
        cons = _eok(r["consensus"]) if r["consensus"] is not None else "-"
        act = _eok(r["actual"]) if r["actual"] is not None else "실제치 미입력"
        sur = f" ({r['surprise']:+.1f}% {'상회' if r['surprise'] > 0 else '하회' if r['surprise'] < 0 else '부합'})" if r["surprise"] is not None else ""
        px = f" / 주가 {r['pct']:+.2f}%" if r["pct"] is not None else ""
        lines.append(f"- {r['name']} 영업이익 예상 {cons} → 실제 {act}{sur}{px}" + (f" · {r['note']}" if r["note"] else ""))
    return lines


def build_data(now: datetime) -> dict:
    """사진·본문이 함께 쓰는 구조화 자료: {'tonight': [(라벨, [항목])], 'tomorrow': {...} | None}."""
    today = now.date()
    tomorrow = next_trading_day(today)

    def items(day: dict) -> list[tuple[str, list[str]]]:
        return [(label, day[key]) for key, label in FIELDS if day.get(key)]

    nxt = items(_day(tomorrow))
    label = f"{tomorrow.month}월 {tomorrow.day}일({'월화수목금토일'[tomorrow.weekday()]})"
    if tomorrow != today + timedelta(days=1):
        label += " · 다음 거래일"
    return {
        "today": f"{today.month}월 {today.day}일({'월화수목금토일'[today.weekday()]})",
        "tonight": items(_day(today)),
        "tomorrow": {"label": label, "items": nxt} if (nxt or _month(tomorrow)) else None,
    }


def _rows_html(items: list[tuple[str, list[str]]]) -> str:
    rows = []
    for label, values in items:
        chips = "".join(
            f'<span class="chip{" star" if v.startswith("★") else ""}">{html.escape(v.lstrip("★"))}</span>'
            for v in values
        )
        rows.append(f'<div class="row"><div class="lab">{html.escape(label.split("(")[0])}</div><div class="chips">{chips}</div></div>')
    return "".join(rows) or '<div class="none">주요 일정 없음</div>'


def render_image(now: datetime, path: Path) -> Path | None:
    """오늘 저녁 발표·내일 일정을 사진(JPG)으로 만든다. 캘린더 자료가 없으면 None."""
    data = build_data(now)
    if not data["tonight"] and not data["tomorrow"] and not earnings_results(now):
        return None
    sections = ""
    results = earnings_results(now)
    if results:
        rows = "".join(
            f'<div class="row"><div class="lab">{html.escape(r["name"])}</div><div class="chips"><span class="chip star">'
            + html.escape(_result_lines([r])[0].split(" ", 2)[2].replace(r["name"], "", 1).strip()) + "</span></div></div>"
            for r in results)
        sections += f'<section><h2>오늘 실적발표 <small>영업이익 예상 vs 실제 · 주가</small></h2>{rows}</section>'
    if data["tonight"]:
        sections += f'<section><h2>오늘 저녁 발표 실적·주요 이슈 <small>{data["today"]}</small></h2>{_rows_html(data["tonight"])}</section>'
    if data["tomorrow"]:
        sections += f'<section><h2>내일 일정 <small>{html.escape(data["tomorrow"]["label"])}</small></h2>{_rows_html(data["tomorrow"]["items"])}</section>'
    page_html = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><style>
body{{margin:0;padding:14px;width:452px;background:#f4f5f7;color:#1b1f24;font-family:"Malgun Gothic","Noto Sans CJK KR","Apple SD Gothic Neo",sans-serif}}
h1{{margin:0 0 10px;font-size:17px}}h1 small{{color:#7a828c;font-weight:400;font-size:12px;margin-left:6px}}
section{{background:#fff;border:1px solid #e7e9ec;border-radius:12px;padding:12px 14px;margin-bottom:10px}}
h2{{margin:0 0 8px;font-size:14px;border-left:4px solid #ffbc00;padding-left:8px}}h2 small{{color:#7a828c;font-weight:400;margin-left:4px}}
.row{{display:flex;gap:10px;padding:6px 0;border-top:1px solid #e7e9ec}}.row:first-of-type{{border-top:0}}
.lab{{flex:0 0 84px;font-size:12px;color:#15803d;font-weight:700;line-height:1.5}}
.chips{{flex:1;display:flex;flex-wrap:wrap;gap:5px}}
.chip{{font-size:13px;background:#f4f5f7;border-radius:6px;padding:2px 7px;line-height:1.5}}
.chip.star{{background:#fff6dc;font-weight:700}}.chip.star::before{{content:"★ ";color:#e08a00}}
.none{{font-size:13px;color:#7a828c}}.foot{{font-size:11px;color:#7a828c;line-height:1.5}}
</style><body><h1>증시 일정<small>KB 시장 리포트</small></h1>{sections}
<div class="foot">★ 핵심 · 증시 캘린더 기준, 미국 실적은 한국시간 밤~새벽 발표 · 해외 일정은 현지시각이며 변경될 수 있습니다</div></body></html>"""
    from playwright.sync_api import sync_playwright  # blog_post가 이 모듈을 쓸 때는 필요 없으므로 늦게 불러온다

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=True)
        except Exception:  # Edge가 없는 환경(로컬 테스트 등)은 기본 Chromium으로
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 480, "height": 400}, device_scale_factor=2.5)
        page.set_content(page_html)
        page.screenshot(path=str(path), type="jpeg", quality=90, full_page=True)
        browser.close()
    return path


def build_section(now: datetime) -> list[str]:
    """리포트 본문에 넣을 줄들 (빈 리스트 = 캘린더 자료 없음)."""
    today = now.date()
    tomorrow = next_trading_day(today)
    lines: list[str] = []
    res = _result_lines(earnings_results(now))
    if res:
        lines += ["", "■ 오늘 실적발표 (영업이익 예상 vs 실제 · 주가 등락)", *res]
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
