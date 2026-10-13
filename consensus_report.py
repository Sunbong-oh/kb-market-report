"""증권사 리포트 목표주가 상향·하향 정리 (한경 컨센서스 기업 리포트, 평일 아침 자동).

1. 한경 컨센서스(consensus.hankyung.com)에서 오늘 새로 올라온 기업 리포트 목록을 받는다.
2. 리포트마다 같은 증권사가 같은 종목에 낸 직전 리포트(최근 1년)를 찾아 목표주가를 비교한다.
3. 상향 / 하향 / 투자의견 변경 / 신규·비교불가로 나눠 KB 스타일 글로 만든다.
4. consensus/latest.md, consensus/YYYY-MM-DD.md 에 저장하고 텔레그램으로 보낸다.

실행: python consensus_report.py [--date 2026-10-12] [--no-send] [--no-publish]
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


def build_text(day: date, changes: list[Change], made: datetime) -> tuple[str, str]:
    up = sorted([c for c in changes if c.pct is not None and c.pct > 0], key=lambda c: -c.pct)
    down = sorted([c for c in changes if c.pct is not None and c.pct < 0], key=lambda c: c.pct)
    keep = [c for c in changes if c.pct == 0]
    new = [c for c in changes if c.pct is None]
    op_changed = [c for c in changes if c.opinion_changed]
    weekday = "월화수목금토일"[day.weekday()]

    title = f"{day:%y}년 {day.month}월 {day.day}일 증권사 리포트 목표주가 변동"
    L = [f"■ {day.month}월 {day.day}일({weekday}) 신규 기업 리포트 {len(changes)}건",
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
    L.append(f"(데이터: 한경 컨센서스 · 같은 증권사 직전 리포트 대비 · {made:%Y-%m-%d %H:%M} 기준)")
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


def save(day: date, title: str, body: str) -> list[Path]:
    OUT_DIR.mkdir(exist_ok=True)
    md = f"# {title}\n\n```\n{body}\n```\n"
    paths = [OUT_DIR / "latest.md", OUT_DIR / f"{day:%Y-%m-%d}.md"]
    for p in paths:
        p.write_text(md, encoding="utf-8")
    return paths


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


def main() -> int:
    load_dotenv(ROOT / ".env")
    made = datetime.now(KST).replace(tzinfo=None)
    day = made.date()
    if "--date" in sys.argv:
        day = date.fromisoformat(sys.argv[sys.argv.index("--date") + 1])

    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        today = fetch_list(client, day.isoformat(), day.isoformat())
        today = [r for r in today if r.day == day.isoformat()]
        print(f"{day} 기업 리포트 {len(today)}건")
        if not today:
            print("오늘 올라온 기업 리포트가 없습니다 (휴일이거나 아직 업로드 전) - 보내지 않습니다.")
            return 0
        # 종목별 검색은 한경이 조건을 무시해 종목마다 수십 쪽을 읽게 되므로(5건에 8분),
        # 최근 목록을 한 번만 받아 지난 기록과 합친 뒤 그 안에서 직전 리포트를 찾는다
        start = (day - timedelta(days=LOOKBACK_DAYS)).isoformat()
        recent = fetch_list(client, start, (day - timedelta(days=1)).isoformat())
        print(f"직전 비교용 최근 리포트 {len(recent)}건 (가장 오래된 날 {min((r.day for r in recent), default='-')})")
        pool = load_history() + recent
        changes = [Change(r, find_previous(r, pool)) for r in today]
    save_history(pool + today, day)

    title, body = build_text(day, changes, made)
    print(title)
    print(body)
    for p in save(day, title, body):
        print(f"저장: {p}")
    if "--no-send" not in sys.argv:
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
