"""장마감 수급체크 블로그 준비물 저장 (report.py가 15:50 리포트 때 함께 호출).

일일 대시보드(C:\\CLAUDE\\1\\dashboard.py)와 같은 blog_post 폴더에, 파일 이름이 겹치지 않게 저장한다.
    제목_장마감수급.txt              26년 9월 30일 국내 장마감 수급 체크
    본문_장마감수급.txt              지수·환율·금리·투자자별 수급 요약 초안
    YYYY-MM-DD_장마감수급_1_시장.jpg  리포트 사진 2장
    YYYY-MM-DD_장마감수급_2_수급.jpg
금액 단위는 억원 (KB IVSA0070 투자자별 순매수, HTS 0783 화면과 일치 확인).
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from market_calendar import build_section as calendar_section

BLOG_DIR = Path(os.environ.get("BLOG_DIR", r"C:\CLAUDE\1\blog_post"))
IMAGE_NAMES = ["1_시장", "2_수급", "3_강세테마"]
BIG3 = [("외국인", "외국인"), ("기관계", "기관"), ("개인", "개인")]


def _n(v, d=0) -> str:
    return "-" if v is None else f"{v:,.{d}f}"


def _s(v, d=0) -> str:
    return "-" if v is None else f"{v:+,.{d}f}"


def _move(change, pct=None, d=2) -> str:
    if change is None:
        return ""
    arrow = "▲" if change > 0 else "▼" if change < 0 else "-"
    return f"{arrow}{abs(change):,.{d}f}" + (f", {pct:+.2f}%" if pct is not None else "")


def title_for(now: datetime) -> str:
    return f"{now:%y}년 {now.month}월 {now.day}일 국내 장마감 수급 체크"


def _flow_line(inv: dict[str, dict], key: str) -> str:
    return " / ".join(f"{label} {_s(inv.get(name, {}).get(key))}" for name, label in BIG3)


def _derivs_reset(inv: dict[str, dict]) -> bool:
    """선물·옵션 수급이 전부 0 = KB가 장 마감 뒤 초기화한 상태 (늦게 실행된 리포트)."""
    return all(not (inv.get(name, {}).get(key)) for name, _ in BIG3 for key in ("futures", "call", "put"))


def _summary(inv: dict[str, dict], kospi: dict | None) -> str:
    f = inv.get("외국인", {})
    spot, fut = f.get("kospi"), f.get("futures")
    if spot is None or fut is None:
        return ""
    word = lambda v: "순매수" if v > 0 else "순매도"  # noqa: E731
    if _derivs_reset(inv) or not fut:
        who = f"외국인 코스피 현물 {word(spot)}"
    elif (spot > 0) == (fut > 0):
        who = f"외국인 현물·선물 동반 {word(spot)}"
    else:
        who = f"외국인 현물 {word(spot)}·선물 {word(fut)}"
    idx = ""
    if kospi and kospi.get("change_pct") is not None:
        idx = f", 코스피 {kospi['change_pct']:+.2f}% {'상승' if kospi['change_pct'] > 0 else '하락' if kospi['change_pct'] < 0 else '보합'} 마감"
    return who + idx


def build_body(api: dict, now: datetime) -> str:
    market = api.get("/api/market") or {}
    macro = api.get("/api/macro") or {}
    idx = {i.get("id"): i for i in market.get("indices", [])}
    inv = {i["name"]: i for i in market.get("investors", [])}
    kospi, kosdaq, nq = idx.get("KGG01P"), idx.get("QGG01P"), idx.get("CME@NQ")

    lines = [title_for(now), ""]
    lines.append("■ 지수")
    for label, i in (("코스피", kospi), ("코스닥", kosdaq), ("나스닥 선물", nq)):
        if i:
            lines.append(f"- {label} {_n(i.get('value'), 2)} ({_move(i.get('change'), i.get('change_pct'))})")
    usd, tsy = macro.get("usd_krw"), macro.get("treasury_10y")
    if usd:
        lines.append(f"- 원/달러 {_n(usd.get('rate'), 2)}원 ({_move(usd.get('change'), usd.get('change_pct'))})")
    if tsy:
        y, ch = tsy.get("yield"), tsy.get("change")
        move = ""
        if y is not None and ch is not None:
            prev = y - ch
            move = f" ({ch * 100:+.1f}bp" + (f", {ch / prev * 100:+.2f}%" if prev else "") + ")"
        lines.append(f"- 국고채 10년 {_n(y, 3)}%{move}")

    if inv:
        lines += ["", "■ 투자자별 순매수 (억원)",
                  f"- 코스피: {_flow_line(inv, 'kospi')}",
                  f"- 코스닥: {_flow_line(inv, 'kosdaq')}",
                  "", "■ 선물·옵션 (KOSPI200, 억원)"]
        f = market.get("futures") or {}
        if f.get("price") is not None:
            lines.append(f"- {f.get('name', 'KOSPI200 선물')} {_n(f.get('price'), 2)} ({_move(f.get('change'), f.get('change_pct'))})")
        if _derivs_reset(inv):
            lines.append("- 선물·옵션 수급: 장 마감 약 1시간 뒤 KB에서 초기화되어 표시할 값이 없습니다.")
        else:
            lines.append(f"- 선물: {_flow_line(inv, 'futures')}")
            lines.append(f"- 콜옵션: {_flow_line(inv, 'call')}")
            lines.append(f"- 풋옵션: {_flow_line(inv, 'put')}")

    th = api.get("/api/themes") or {}
    if th.get("themes"):
        lines += ["", "■ 오늘의 강세 테마" + (" (네이버 금융 테마 등락률 상위)" if th.get("source") == "naver" else " (KB 업종랭킹)")]
        short = [re.sub(r"\(.*?\)", "", t["name"]).strip() for t in th["themes"][:3]]
        lines.append(f"오늘은 {' · '.join(short)} 테마가 강세였습니다.")
        for i, t in enumerate(th["themes"], 1):
            line = f"{i}. {t['name']} {_s(t.get('change_pct'), 2)}%"
            notes = []
            if t.get("total"):
                notes.append(f"{t['total']}종목 중 {t.get('rise', 0)} 상승" if t.get("fall") else f"{t['total']}종목 모두 상승")
            if t.get("leaders"):
                notes.append("주도주 " + ", ".join(f"{s['name']} {_s(s.get('change_pct'), 2)}%" for s in t["leaders"]))
            lines.append(line + (" — " + " · ".join(notes) if notes else ""))

    breadth = market.get("breadth") or {}
    if breadth:
        lines += ["", "■ 등락 종목수"]
        for label, key in (("코스피", "kospi"), ("코스닥", "kosdaq")):
            b = breadth.get(key) or {}
            up, down = (b.get("up") or 0) + (b.get("ulmt") or 0), (b.get("dwn") or 0) + (b.get("llmt") or 0)
            lines.append(f"- {label}: 상승 {up:,.0f} (상한 {b.get('ulmt') or 0:,.0f}) / 보합 {b.get('unchng') or 0:,.0f} / "
                         f"하락 {down:,.0f} (하한 {b.get('llmt') or 0:,.0f})")

    try:
        lines += calendar_section(now)  # 오늘 저녁 발표 실적·이슈, 내일 일정 (증시 캘린더)
    except Exception as exc:  # 캘린더 때문에 리포트가 막히지 않게
        print(f"캘린더 섹션 건너뜀: {exc}")

    summary = _summary(inv, kospi)
    if summary:
        lines += ["", f"▶ 한 줄 요약: {summary}"]
    lines += ["", f"(데이터: KB증권 OpenAPI · {now:%Y-%m-%d %H:%M} 기준)"]
    return "\n".join(lines) + "\n"


def save_blog_post(api: dict, shots: list[tuple[Path, str]], now: datetime) -> Path:
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    (BLOG_DIR / "제목_장마감수급.txt").write_text(title_for(now), encoding="utf-8")
    (BLOG_DIR / "본문_장마감수급.txt").write_text(build_body(api, now), encoding="utf-8")
    for (path, _), name in zip(shots, IMAGE_NAMES):
        shutil.copy(path, BLOG_DIR / f"{now:%Y-%m-%d}_장마감수급_{name}.jpg")
    print("블로그 준비물 저장:", title_for(now), "→", BLOG_DIR)
    return BLOG_DIR
