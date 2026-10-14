"""증권사 리포트 목표주가 상향·하향 정리 (한경 컨센서스 기업 리포트, 평일 아침 자동).

1. 한경 컨센서스(consensus.hankyung.com)에서 오늘 새로 올라온 기업 리포트 목록을 받는다.
2. 리포트마다 같은 증권사가 같은 종목에 낸 직전 리포트(최근 1년)를 찾아 목표주가를 비교한다.
3. 상향 / 하향 / 투자의견 변경 / 신규·비교불가로 나눠 KB 스타일 글로 만든다.
4. consensus/latest.md, consensus/YYYY-MM-DD.md 에 저장하고 텔레그램으로 보낸다.

실행: python consensus_report.py [--date 2026-10-12] [--no-publish]   (텔레그램은 --send 를 줄 때만)
필요한 환경변수(.env 또는 GitHub Secrets): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "consensus"
HISTORY = OUT_DIR / "history.json"
REPORTED = OUT_DIR / "reported.json"  # 이미 정리해 보낸 리포트 (다음 날 중복 방지)
KST = timezone(timedelta(hours=9))
BASE = "https://consensus.hankyung.com"
LIST_URL = f"{BASE}/analysis/list"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/128.0 Safari/537.36",
    "Referer": f"{BASE}/",
}
PAGE_SIZE = 80
LOOKBACK_DAYS = 365   # 직전 리포트를 찾는 기간
TG_LIMIT = 3900       # 텔레그램 메시지 한 통 최대 4096자 → 여유를 두고 나눈다


@dataclass
class Report:
    day: str            # 2026-10-12
    title: str
    company: str
    code: str           # 005930 (없으면 "")
    target: int | None  # 목표주가(원), 없으면 None
    opinion: str
    analyst: str
    broker: str
    summary: list[str]
    pdf: str
    source: str = "한경"  # 한경 / 네이버


@dataclass
class Change:
    now: Report
    prev: Report | None

    @property
    def pct(self) -> float | None:
        if not self.prev or not self.prev.target or not self.now.target:
            return None
        return (self.now.target / self.prev.target - 1) * 100

    @property
    def opinion_changed(self) -> bool:
        if not self.prev:
            return False
        a, b = _norm_opinion(self.prev.opinion), _norm_opinion(self.now.opinion)
        return bool(a and b and a != b)  # '투자의견없음'·NR 이 끼면 변경으로 보지 않는다


# ── 한경 컨센서스 목록 읽기 ─────────────────────────────────────────────

def _decode(res: httpx.Response) -> str:
    """한경 컨센서스는 페이지에 따라 EUC-KR(CP949)일 수 있어 헤더·meta 순서로 인코딩을 고른다."""
    m = re.search(r"charset=([\w-]+)", res.headers.get("content-type", ""), re.I) \
        or re.search(rb"charset=[\"']?([\w-]+)", res.content[:4000], re.I)
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)).lower() if m else "utf-8"
    if enc in ("euc-kr", "euckr", "ks_c_5601-1987"):
        enc = "cp949"
    return res.content.decode(enc, errors="replace")


def _price(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits and int(digits) > 0 else None


def _split_title(title: str) -> tuple[str, str, str]:
    """'삼성전자(005930)3Q26 Review: ...' → (삼성전자, 005930, 3Q26 Review: ...)"""
    m = re.match(r"\s*(.+?)\s*\(([0-9A-Z]{6})\)\s*[-:]?\s*(.*)", title)
    if m:
        return m.group(1).strip(), m.group(2), m.group(3).strip() or title
    return title.split("-")[0].strip(), "", title


def parse_list(html: str) -> list[Report]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.table_style01") or soup.find("table")
    if table is None:
        return []
    heads = [th.get_text(strip=True) for th in table.select("thead th")]

    def col(*names: str) -> int | None:
        for i, h in enumerate(heads):
            if any(n in h for n in names):
                return i
        return None

    # 머리글 이름으로 열을 찾는다 (없으면 한경 컨센서스 기본 순서)
    idx = {
        "day": col("작성일", "일자"), "title": col("제목"), "target": col("적정가격", "목표가"),
        "opinion": col("투자의견"), "analyst": col("작성자"), "broker": col("제공출처", "증권사"),
    }
    if idx["title"] is None:
        idx = {"day": 0, "title": 2, "target": 3, "opinion": 4, "analyst": 5, "broker": 6}

    reports = []
    for tr in table.select("tbody tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) <= max(v for v in idx.values() if v is not None):
            continue

        def cell(key: str) -> str:
            i = idx.get(key)
            return tds[i].get_text(" ", strip=True) if i is not None else ""

        title_td = tds[idx["title"]]
        head = title_td.find(["strong", "a"])
        title = (head or title_td).get_text(" ", strip=True)
        summary = [li.get_text(" ", strip=True) for li in title_td.select("li") if li.get_text(strip=True)]
        company, code, title_only = _split_title(title)
        pdf = ""
        for a in tr.find_all("a", href=True):
            if "downpdf" in a["href"] or a["href"].lower().endswith(".pdf"):
                pdf = a["href"] if a["href"].startswith("http") else BASE + a["href"]
                break
        if not code:  # 기업정보 링크에 종목코드가 들어 있는 경우
            m = re.search(r"business_code=([0-9A-Z]{6})", str(tr))
            code = m.group(1) if m else ""
        day = re.search(r"\d{4}-\d{2}-\d{2}", cell("day"))
        reports.append(Report(
            day=day.group(0) if day else cell("day"), title=title_only, company=company, code=code,
            target=_price(cell("target")), opinion=cell("opinion"), analyst=cell("analyst"),
            broker=cell("broker"), summary=summary, pdf=pdf,
        ))
    return reports


def fetch_list(client: httpx.Client, sdate: str, edate: str, **extra: str) -> list[Report]:
    """기간 안의 기업 리포트를 모든 페이지에 걸쳐 받는다."""
    out: list[Report] = []
    for page in range(1, 60):
        params = {"sdate": sdate, "edate": edate, "now_page": page, "report_type": "CO",
                  "pagenum": PAGE_SIZE, "order_type": "", "search_value": "", "search_text": "",
                  "business_code": "", **extra}
        res = client.get(LIST_URL, params=params)
        res.raise_for_status()
        rows = parse_list(_decode(res))
        out.extend(rows)
        if len(rows) < PAGE_SIZE:
            break
        time.sleep(0.4)
    return out


# ── 네이버 증권 리서치 (종목분석 리포트) ─────────────────────────────────
# 한경 컨센서스에 올라오지 않는 증권사 리포트가 있어(예: 2026-10-13 에이피알 교보·신한) 두 번째 출처로 함께 본다.
# 예전 finance.naver.com/research 는 Npay 증권 화면으로 옮겨져 표가 없으므로 Npay 증권 JSON API를 쓴다.
#   목록   m.stock.naver.com/api/research/company?page=&pageSize=   (최신순, 날짜 조건 없음)
#   종목별 m.stock.naver.com/api/research/stock/{종목코드}?page=&pageSize=
#   상세   m.stock.naver.com/api/research/company/{researchId}      (opinion, goalPrice, attachUrl)
NAVER_API = "https://m.stock.naver.com/api/research/"


def _naver_json(client: httpx.Client, path: str, **params):
    res = client.get(NAVER_API + path, params=params or None, headers={"Referer": "https://m.stock.naver.com/"})
    res.raise_for_status()
    return res.json()


def _naver_report(x: dict) -> tuple[Report, int]:
    return Report(day=x.get("writeDate", ""), title=x.get("title", ""), company=x.get("itemName", ""),
                  code=x.get("itemCode", ""), target=None, opinion="", analyst="", broker=x.get("brokerName", ""),
                  summary=[], pdf=x.get("endUrl", "") or f"https://m.stock.naver.com/research/company/{x['researchId']}",
                  source="네이버"), x["researchId"]


def fill_naver_detail(client: httpx.Client, rep: Report, rid: int) -> Report:
    """상세 API의 goalPrice·opinion 을 채운다 (원문 PDF 주소가 있으면 링크로 쓴다)."""
    try:
        c = _naver_json(client, f"company/{rid}").get("researchContent") or {}
    except (httpx.HTTPError, ValueError) as exc:
        print(f"네이버 상세 실패 {rep.company}: {exc}")
        return rep
    rep.target = _price(str(c.get("goalPrice") or ""))
    rep.opinion = (c.get("opinion") or "").strip()
    rep.pdf = c.get("attachUrl") or rep.pdf
    time.sleep(0.2)
    return rep


def fetch_naver(client: httpx.Client, sdate: str, edate: str) -> list[tuple[Report, int]]:
    """최신순 목록을 sdate 이전 날짜가 나올 때까지 넘긴다."""
    out: list[tuple[Report, int]] = []
    for page in range(1, 20):
        items = _naver_json(client, "company", page=page, pageSize=100)
        if not items:
            break
        out += [_naver_report(x) for x in items if sdate <= x.get("writeDate", "") <= edate]
        if min(x.get("writeDate", "") for x in items) < sdate:
            break
        time.sleep(0.3)
    return out


def naver_previous(client: httpx.Client, rep: Report) -> Report | None:
    """네이버에서 같은 종목의 지난 리포트를 보고, 같은 증권사의 직전 리포트 목표가를 읽는다."""
    try:
        items = _naver_json(client, f"stock/{rep.code}", page=1, pageSize=40)
    except (httpx.HTTPError, ValueError) as exc:
        print(f"네이버 지난 리포트 실패 {rep.company}: {exc}")
        return None
    same = [_naver_report(x) for x in items
            if x.get("writeDate", "") < rep.day and _norm_broker(x.get("brokerName", "")) == _norm_broker(rep.broker)]
    if not same:
        return None
    prev, rid = max(same, key=lambda t: t[0].day)
    return fill_naver_detail(client, prev, rid)


def add_naver(client: httpx.Client, since: date, day: date, rows: list[Report]) -> list[Report]:
    """한경 목록에 없는 네이버 리포트만 상세(목표가·투자의견)를 읽어 덧붙인다. 실패해도 한경 결과는 그대로."""
    have = {(r.day, r.code, _norm_broker(r.broker)) for r in rows}
    try:
        listed = fetch_naver(client, since.isoformat(), day.isoformat())
    except (httpx.HTTPError, ValueError) as exc:
        print(f"네이버 리서치 목록 실패 (한경만 사용): {exc}")
        return []
    extra = [fill_naver_detail(client, r, rid) for r, rid in listed
             if r.day and (r.day, r.code, _norm_broker(r.broker)) not in have]
    print(f"네이버 리서치 {len(listed)}건 중 한경에 없는 리포트 {len(extra)}건 추가")
    return extra


def load_history() -> list[Report]:
    """지난 실행에서 모아 둔 리포트 기록 (consensus/history.json) - 한경 목록 2달치보다 오래된 직전 리포트용."""
    try:
        return [Report(**r) for r in json.loads(HISTORY.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def save_history(rows: list[Report], day: date) -> None:
    cutoff = (day - timedelta(days=LOOKBACK_DAYS)).isoformat()
    keep = {(r.day, r.code or r.company, _norm_broker(r.broker), r.title): r for r in rows if r.day >= cutoff}
    data = [{**asdict(r), "summary": []} for r in sorted(keep.values(), key=lambda r: r.day)]
    OUT_DIR.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def find_previous(rep: Report, pool: list[Report]) -> Report | None:
    """같은 증권사가 같은 종목에 낸, 오늘 이전의 가장 최근 리포트."""
    same = [r for r in pool
            if (r.code == rep.code if rep.code else r.company == rep.company)
            and _norm_broker(r.broker) == _norm_broker(rep.broker) and r.day < rep.day]
    return max(same, key=lambda r: r.day) if same else None


def _norm_broker(name: str) -> str:
    return re.sub(r"\s|증권|투자|\(주\)|㈜", "", name)


def _norm_opinion(op: str) -> str:
    op = op.strip().lower().replace(" ", "")
    return {"매수": "buy", "strongbuy": "buy", "outperform": "buy", "overweight": "buy", "tradingbuy": "buy",
            "중립": "hold", "neutral": "hold", "marketperform": "hold", "보유": "hold",
            "매도": "sell", "underperform": "sell", "reduce": "sell",
            "투자의견없음": "", "없음": "", "nr": "", "notrated": "", "-": ""}.get(op, op)


# ── KB 스타일 글 ──────────────────────────────────────────────────────

def _won(v: int | None) -> str:
    return f"{v:,}" if v else "-"


def _line(no: int, c: Change) -> list[str]:
    r, p = c.now, c.prev
    op = r.opinion or "-"
    if c.opinion_changed:
        op = f"{p.opinion}→{r.opinion}"
    out = [f"{no}. {r.company} {_won(p.target)} → {_won(r.target)}원 ({c.pct:+.1f}%) · {r.broker} · {op}",
           f"   「{r.title}」"]
    if r.summary and r.summary[0] != r.title:
        out.append(f"   - {r.summary[0][:90]}")
    return out


def build_text(day: date, changes: list[Change], made: datetime, since: date | None = None) -> tuple[str, str]:
    up = sorted([c for c in changes if c.pct is not None and c.pct > 0], key=lambda c: -c.pct)
    down = sorted([c for c in changes if c.pct is not None and c.pct < 0], key=lambda c: c.pct)
    keep = [c for c in changes if c.pct == 0]
    new = [c for c in changes if c.pct is None]
    op_changed = [c for c in changes if c.opinion_changed]
    weekday = "월화수목금토일"[day.weekday()]

    title = f"{day:%y}년 {day.month}월 {day.day}일 증권사 리포트 목표주가 변동"
    span = f"{since.month}/{since.day}~{day.month}/{day.day} {made:%H:%M}" if since else f"{day.month}/{day.day}"
    L = [f"■ {day.month}월 {day.day}일({weekday}) 신규 기업 리포트 {len(changes)}건 (전 섹터 · {span} 발간분)",
         f"- 목표주가 상향 {len(up)} / 하향 {len(down)} / 유지 {len(keep)} / 신규·비교불가 {len(new)}", ""]

    L.append(f"■ 목표주가 상향 ({len(up)}건)")
    for i, c in enumerate(up, 1):
        L += _line(i, c)
    if not up:
        L.append("- 없음")
    L.append("")

    L.append(f"■ 목표주가 하향 ({len(down)}건)")
    for i, c in enumerate(down, 1):
        L += _line(i, c)
    if not down:
        L.append("- 없음")
    L.append("")

    if op_changed:
        L.append(f"■ 투자의견 변경 ({len(op_changed)}건)")
        for c in op_changed:
            L.append(f"- {c.now.company}: {c.prev.opinion} → {c.now.opinion} · {c.now.broker}")
        L.append("")

    if new:
        L.append(f"■ 신규 커버리지·직전 비교불가 ({len(new)}건)")
        L.append("- " + " · ".join(
            f"{c.now.company}({c.now.broker}{', TP ' + _won(c.now.target) if c.now.target else ''})" for c in new))
        L.append("")

    if up or down:
        lead = "상향 우위" if len(up) > len(down) else "하향 우위" if len(down) > len(up) else "상향·하향 균형"
        parts = [f"{lead}({len(up)}:{len(down)})"]
        if up:
            parts.append(f"최대 상향 {up[0].now.company} {up[0].pct:+.1f}%")
        if down:
            parts.append(f"최대 하향 {down[0].now.company} {down[0].pct:+.1f}%")
        L.append("▶ 한 줄 요약: " + ", ".join(parts))
    else:
        L.append("▶ 한 줄 요약: 목표주가를 바꾼 리포트가 없었습니다")
    L.append("")
    L.append(f"(데이터: 한경 컨센서스·네이버 증권 리서치 · 같은 증권사 직전 리포트 대비 · {made:%Y-%m-%d %H:%M} 기준)")
    return title, "\n".join(L)


# ── 저장·전송 ─────────────────────────────────────────────────────────

def send_telegram_text(text: str) -> None:
    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(".env에 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID를 넣어 주세요.")
    chunks, cur = [], ""
    for line in text.splitlines():  # 줄 단위로 나눠 4096자 제한을 넘지 않게
        if len(cur) + len(line) + 1 > TG_LIMIT:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for chunk in chunks:
        res = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         data={"chat_id": chat_id, "text": chunk}, timeout=30)
        if res.status_code != 200 or not res.json().get("ok"):
            raise RuntimeError(f"텔레그램 메시지 전송 실패: HTTP {res.status_code} {res.text[:200]}")


def _item(c: Change) -> dict:
    r, p = c.now, c.prev
    return {"company": r.company, "code": r.code, "broker": r.broker, "title": r.title, "day": r.day,
            "target": r.target, "prev_target": p.target if p else None, "prev_day": p.day if p else None,
            "pct": round(c.pct, 2) if c.pct is not None else None, "opinion": r.opinion,
            "prev_opinion": p.opinion if p else None, "opinion_changed": c.opinion_changed,
            "summary": r.summary[:3], "pdf": r.pdf, "source": r.source}


def save(day: date, title: str, body: str, changes: list[Change], made: datetime, since: date) -> list[Path]:
    """사람이 읽는 .md 와 홈페이지(market-dashboard)가 읽는 .json 을 함께 저장한다."""
    OUT_DIR.mkdir(exist_ok=True)
    md = f"# {title}\n\n```\n{body}\n```\n"
    data = {"day": day.isoformat(), "since": since.isoformat(), "made": f"{made:%Y-%m-%d %H:%M}", "title": title,
            "body": body, "items": [_item(c) for c in changes]}
    js = json.dumps(data, ensure_ascii=False, indent=1)
    paths = []
    for name, text in ((f"latest.md", md), (f"{day:%Y-%m-%d}.md", md), ("latest.json", js), (f"{day:%Y-%m-%d}.json", js)):
        (OUT_DIR / name).write_text(text, encoding="utf-8")
        paths.append(OUT_DIR / name)
    return paths


def load_reported() -> dict[str, str]:
    """{리포트 키: 처음 정리한 날}. 같은 날 다시 돌리면(수동 재실행) 그날 리포트는 다시 넣는다."""
    try:
        data = json.loads(REPORTED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):  # 예전 형식(키 목록, 한 번의 실행분) → 그 실행일 = 가장 늦은 리포트 날짜
        run = max((k[:10] for k in data), default="")
        return {k: run for k in data}
    return data


def save_reported(reported: dict[str, str], keys: set[str], day: date) -> None:
    cutoff = (day - timedelta(days=14)).isoformat()
    merged = {**{k: day.isoformat() for k in keys}, **reported}  # 먼저 정리한 날을 유지
    REPORTED.write_text(json.dumps({k: v for k, v in sorted(merged.items()) if k[:10] >= cutoff},
                                   ensure_ascii=False, indent=0), encoding="utf-8")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120, check=check)


def publish(day: date) -> str:
    if not (ROOT / ".git").exists() or not git("remote", check=False).stdout.strip():
        return "건너뜀: GitHub 저장소(origin)가 연결되지 않았습니다"
    git("add", "consensus")  # 리포트 결과물만
    if not git("diff", "--cached", "--name-only").stdout.strip():
        return "변경 없음"
    git("commit", "-m", f"목표주가 변동 리포트 {day:%Y-%m-%d}")
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    git("pull", "--rebase", "origin", branch, check=False)
    git("push", "-u", "origin", branch)
    return "GitHub 업로드 완료"


def _key(r: Report) -> str:
    return f"{r.day}|{r.code or r.company}|{_norm_broker(r.broker)}|{r.title}"


def prev_business_day(day: date) -> date:
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def main() -> int:
    load_dotenv(ROOT / ".env")
    made = datetime.now(KST).replace(tzinfo=None)
    day = made.date()
    if "--date" in sys.argv:
        day = date.fromisoformat(sys.argv[sys.argv.index("--date") + 1])
    # 오전 8시에는 그날 리포트가 거의 올라오지 않으므로, 전 영업일부터 지금까지 올라온 리포트 중
    # 지난 정리에 아직 들어가지 않은 것을 모은다 (전날 아침 정리 뒤에 올라온 리포트 + 오늘 아침 리포트)
    since = prev_business_day(day)
    reported = load_reported()

    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        rows = fetch_list(client, since.isoformat(), day.isoformat())
        rows += add_naver(client, since, day, rows)
        new = [r for r in rows if since.isoformat() <= r.day <= day.isoformat() and reported.get(_key(r), "9999") >= day.isoformat()]
        print(f"{since}~{day} 기업 리포트 {len(rows)}건 중 새로 정리할 리포트 {len(new)}건")
        if not new:
            print("새로 올라온 기업 리포트가 없습니다 (휴일이거나 아직 업로드 전) - 보내지 않습니다.")
            return 0
        # 종목별 검색은 한경이 조건을 무시해 종목마다 수십 쪽을 읽게 되므로(5건에 8분),
        # 최근 목록을 한 번만 받아 지난 기록과 합친 뒤 그 안에서 직전 리포트를 찾는다
        start = (day - timedelta(days=LOOKBACK_DAYS)).isoformat()
        recent = fetch_list(client, start, (since - timedelta(days=1)).isoformat())
        print(f"직전 비교용 최근 리포트 {len(recent)}건 (가장 오래된 날 {min((r.day for r in recent), default='-')})")
        pool = load_history() + recent + rows
        changes = []
        for r in new:
            prev = find_previous(r, pool)
            if (prev is None or not prev.target) and r.source == "네이버" and r.code:
                prev = naver_previous(client, r) or prev  # 한경 기록에 없으면 네이버의 같은 증권사 직전 리포트
                if prev:
                    pool.append(prev)
            changes.append(Change(r, prev))
    save_history(pool, day)

    title, body = build_text(day, changes, made, since)
    print(title)
    print(body)
    for p in save(day, title, body, changes, made, since):
        print(f"저장: {p}")
    save_reported(reported, {_key(r) for r in new}, day)
    if "--send" in sys.argv:  # 기본은 보내지 않음 (2026-10-14 사용자 요청: 홈페이지에서만 확인)
        send_telegram_text(f"{title}\n\n{body}")
        print("텔레그램 전송 완료")
    if "--no-publish" not in sys.argv:
        try:
            print(publish(day))
        except Exception as exc:
            print(f"GitHub 업로드 실패: {getattr(exc, 'stderr', '') or exc}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
