"""장중 실시간 수급 (GitHub Actions용 예비): 회사 PC가 꺼져 있어도 사이트가 실시간 수급을 보여 주게 한다.

평일 08:00~16:05(KST) 1분마다 리포트 화면이 쓰는 API 응답을 모아 flow-live 브랜치의 snapshot.json을 갱신한다.
- 서버는 리포트 전용 모드(REPORT_ONLY)로 잠깐 켠다 (report.py와 같은 방식).
- 회사 PC가 최근 3분 안에 올린 스냅샷이 있으면 PC 것이 더 정확하므로(선물 분 단위 기록 포함) 건너뛴다.
- PC가 올린 futures_flow.json은 그대로 둔다.
- 오늘 장 데이터가 아직 없으면(개장 전·휴장일) 올리지 않는다 → 사이트는 전일 장마감을 그대로 보여 준다.
- 리포트 전용 서버는 투자자별 선물 순매수를 분 단위로 기록하지 않으므로, 여기서 1분마다 당일 누적값을 쌓아
  /api/futures 의 flows 로 넣는다 (교차차트의 외국인 빨간 선). 작업이 이어 뛸 때는 flow-live에 올라간 기록을 이어 받는다.
- 15:46 이후 한 번, 그날 기록을 main의 archive/날짜/futures_flow.json 으로 저장한다 (장마감 페이지 교차차트용, PC가 이미
  올렸으면 건너뜀).
한 번 실행은 최대 5시간 40분 (Actions 작업 시간 제한). 끝나면 워크플로가 다음 실행을 바로 띄워 하루 종일 이어 달린다
(장 시간이 아니면 기다리기만 한다 - 공개 저장소라 Actions 사용 시간 제한 없음).
"""

from __future__ import annotations

import json
import re
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from market_issues import market_issues

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8000"
KST = timezone(timedelta(hours=9))
BRANCH = "flow-live"
KEYS = ("/api/status", "/api/market", "/api/macro", "/api/indices/intraday", "/api/themes", "/api/futures?minutes=1")
START, END = "08:00", "16:05"
MAX_RUN = timedelta(hours=5, minutes=40)
SESSION = ("0845", "1545")  # 선물 정규장 - 이 시간의 투자자별 누적 순매수를 분 단위로 쌓는다
FLOW_KEYS = ("외국인", "기관계", "개인")


def now() -> datetime:
    return datetime.now(KST)


def git(*args: str, inp: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, input=inp, capture_output=True, timeout=120)


def start_server() -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                            cwd=ROOT, env={**os.environ, "REPORT_ONLY": "1"})
    for _ in range(40):
        try:
            if httpx.get(f"{URL}/api/status", timeout=3).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(1)
    proc.terminate()
    raise RuntimeError("서버를 시작하지 못했습니다.")


def snapshot() -> dict:
    out = {}
    for k in KEYS:
        try:
            r = httpx.get(URL + k, timeout=60)
            if r.status_code == 200:
                out[k] = r.json()
        except httpx.HTTPError as e:
            print("  실패", k, type(e).__name__)
    return out


def has_today(snap: dict, today: str) -> bool:
    market = snap.get("/api/market") or {}
    kospi = (snap.get("/api/indices/intraday") or {}).get("KGG01P") or {}
    return str(market.get("time", ""))[:8] == today or str(kospi.get("date", "")) == today


def remote_tree() -> tuple[dict | None, list[str]]:
    """flow-live의 현재 snapshot.json과 (snapshot.json 외) 파일 줄들."""
    if git("fetch", "-q", "origin", BRANCH).returncode:
        return None, []
    rows = [ln for ln in git("ls-tree", "FETCH_HEAD").stdout.decode().splitlines() if not ln.endswith("\tsnapshot.json")]
    try:
        snap = json.loads(git("show", "FETCH_HEAD:snapshot.json").stdout.decode("utf-8"))
    except ValueError:
        snap = None
    return snap, rows


def pc_active(remote: dict | None, t: datetime) -> bool:
    if not remote or remote.get("source") == "gh" or remote.get("date") != t.strftime("%Y%m%d"):
        return False
    try:
        hh, mm = map(int, str(remote.get("time", "")).split(":"))
    except ValueError:
        return False
    return (t.hour * 60 + t.minute) - (hh * 60 + mm) <= 3


def push(data: str, keep_rows: list[str], extra: dict | None = None) -> None:
    files = {"snapshot.json": data, **(extra or {})}
    rows = "".join(r + "\n" for r in keep_rows if r.split("\t")[-1] not in files)
    for name, body in files.items():
        blob = git("hash-object", "-w", "--stdin", inp=body.encode("utf-8")).stdout.decode().strip()
        rows += f"100644 blob {blob}\t{name}\n"
    tree = git("mktree", inp=rows.encode()).stdout.decode().strip()
    commit = git("commit-tree", tree, "-m", "실시간 수급 (Actions)").stdout.decode().strip()
    res = git("push", "-f", "-q", "origin", f"{commit}:refs/heads/{BRANCH}")
    if res.returncode:
        raise RuntimeError(res.stderr.decode(errors="replace")[:300])


def record_flows(snap: dict, flows: dict, t: datetime) -> None:
    """이번 분의 투자자별 선물 누적 순매수를 flows[HHMM] 에 쌓고, 스냅샷의 /api/futures 에 분 단위 기록으로 넣는다."""
    fut = snap.get("/api/futures?minutes=1")
    if not fut:
        return
    tm = t.strftime("%H%M")
    cur = fut.get("current_flows") or {}
    if SESSION[0] <= tm <= SESSION[1] and any(cur.get(k) is not None for k in FLOW_KEYS):
        flows[tm] = {"t": tm, **{k: cur[k] for k in FLOW_KEYS if cur.get(k) is not None}}
    elif SESSION[1] < tm <= "1605" and flows and any(cur.get(k) is not None for k in FLOW_KEYS):
        # 15:35 동시호가부터 실시간 값이 멈췄다가 장 뒤에 최종값으로 바뀐다 → 15:45 마지막 점을 최신 값으로 계속 갱신
        flows[SESSION[1]] = {"t": SESSION[1], **{k: cur[k] for k in FLOW_KEYS if cur.get(k) is not None}}
    if flows and not fut.get("flows"):
        fut["flows"] = [flows[k] for k in sorted(flows)]
        fut["flow_date"] = t.strftime("%Y%m%d")


def seed_flows(remote: dict | None, today: str) -> dict:
    """앞선 작업이 flow-live에 올린 오늘 분 단위 기록을 이어 받는다."""
    if not remote or remote.get("date") != today:
        return {}
    fut = (remote.get("data") or {}).get("/api/futures?minutes=1") or {}
    if fut.get("flow_date") != today:
        return {}
    return {r["t"]: r for r in fut.get("flows") or [] if r.get("t")}


def save_archive(snap: dict, t: datetime) -> bool:
    """장 마감 뒤 한 번: 그날 선물 1분봉 + 분 단위 기록을 main의 archive/날짜/futures_flow.json 으로 (PC가 올렸으면 건너뜀)."""
    fut = snap.get("/api/futures?minutes=1") or {}
    if fut.get("flow_date") != t.strftime("%Y%m%d") or len(fut.get("flows") or []) < 2:
        return False
    rel = f"archive/{t:%Y-%m-%d}/futures_flow.json"
    git("pull", "-q", "--rebase", "origin", "main")
    path = ROOT / rel
    if path.exists():
        print("이미 저장됨:", rel)
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fut, ensure_ascii=False), encoding="utf-8")
    git("add", rel)
    git("commit", "-q", "-m", f"선물 수급 기록 {t:%Y-%m-%d} (Actions)", "--", rel)
    for _ in range(3):
        if git("push", "-q", "origin", "HEAD:main").returncode == 0:
            print("저장:", rel)
            return True
        git("pull", "-q", "--rebase", "origin", "main")
    print("archive 저장 실패")
    return False


NAVER = "https://m.stock.naver.com/api/stocks/industry"
NAVER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
                 "Referer": "https://m.stock.naver.com/"}


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def _pct(s: dict) -> float | None:
    pct = _num(s.get("fluctuationsRatio"))
    if pct is not None and pct > 0 and (s.get("compareToPreviousPrice") or {}).get("name") in ("FALLING", "LOWER_LIMIT"):
        pct = -pct
    return pct


def _market(s: dict) -> str:
    ex = s.get("stockExchangeType") or {}
    code = str(ex.get("code") or ex.get("nameEng") or s.get("stockExchangeName") or s.get("sosok") or "").upper()
    if code in ("KS", "KOSPI", "0", "KOSPI200"):
        return "kospi"
    if code in ("KQ", "KOSDAQ", "1"):
        return "kosdaq"
    return ""


_logged = False


def market_map(per_sector: int = 30) -> dict | None:
    """네이버 증권 업종(섹터)별 종목 등락 → 사이트 '맵'(트리맵). 시장별로 업종 안 종목을 시가총액 순으로."""
    global _logged
    try:
        with httpx.Client(headers=NAVER_HEADERS, timeout=10, follow_redirects=True) as http:
            groups = http.get(NAVER, params={"page": 1, "pageSize": 100}).json().get("groups", [])
            out = {"kospi": [], "kosdaq": []}
            for g in groups:
                stocks = []
                for page in range(1, 6):  # 반도체처럼 100종목이 넘는 업종은 여러 쪽 (정렬이 등락순이라 1쪽만 보면 대형주가 빠진다)
                    try:
                        rows = http.get(f"{NAVER}/{g['no']}", params={"page": page, "pageSize": 100}).json().get("stocks", [])
                    except Exception:
                        break
                    stocks += rows
                    if len(rows) < 100:
                        break
                if not stocks:
                    continue
                if stocks and not _logged:
                    print("  맵 종목 필드:", {k: stocks[0][k] for k in list(stocks[0])[:25]}, flush=True)
                    _logged = True
                by = {"kospi": [], "kosdaq": []}
                for st in stocks:
                    mk, pct, cap = _market(st), _pct(st), _num(st.get("marketValue"))
                    if mk and pct is not None:
                        by[mk].append({"n": st.get("stockName"), "p": round(pct, 2), "v": cap or 0})
                for mk, rows in by.items():
                    if not rows:
                        continue
                    rows.sort(key=lambda r: r["v"], reverse=True)
                    tot = sum(r["v"] for r in rows) or 0
                    pct = (sum(r["p"] * r["v"] for r in rows) / tot) if tot else sum(r["p"] for r in rows) / len(rows)
                    out[mk].append({"s": g.get("name"), "p": round(pct, 2), "v": tot, "st": rows[:per_sector]})
            for mk in out:
                out[mk].sort(key=lambda r: r["v"], reverse=True)
            print("  맵:", {k: (len(v), sum(len(x["st"]) for x in v)) for k, v in out.items()}, flush=True)
            return out if out["kospi"] or out["kosdaq"] else None
    except Exception as e:
        print("  맵 실패:", type(e).__name__, e, flush=True)
        return None


CODES_URL = "https://sunbong-oh.github.io/market-dashboard/stocks/codes.json"
_codes: tuple[float, list[str]] = (0.0, [])


def watch_prices() -> dict | None:
    """사이트 관심 종목 현재가 (네이버 증권). 종목 목록은 사이트의 stocks/codes.json (30분마다 다시 읽음)."""
    global _codes
    try:
        with httpx.Client(headers=NAVER_HEADERS, timeout=8, follow_redirects=True) as http:
            if time.time() - _codes[0] > 1800:
                _codes = (time.time(), [str(c) for c in http.get(CODES_URL, params={"t": int(time.time())}).json()][:40])
            out = {}
            for code in _codes[1]:
                try:
                    r = http.get(f"https://m.stock.naver.com/api/stock/{code}/basic").json()
                except Exception:
                    continue
                price, chg, pct = _num(r.get("closePrice")), _num(r.get("compareToPreviousClosePrice")), _num(r.get("fluctuationsRatio"))
                if price is None:
                    continue
                if (r.get("compareToPreviousPrice") or {}).get("name") in ("FALLING", "LOWER_LIMIT"):
                    chg, pct = -abs(chg or 0), -abs(pct or 0)
                out[code] = {"p": price, "c": chg or 0, "r": pct or 0, "st": r.get("marketStatus") or "",
                             "at": re.sub(r"\D", "", str(r.get("localTradedAt") or ""))[:12]}
            return out
    except Exception as e:
        print("  관심종목 현재가 실패:", type(e).__name__, e, flush=True)
        return None


def parse_issue_keywords(body: str) -> list[list[str]]:
    """키워드 파일 → 이슈 최대 2개. '---' 줄로 이슈를 나누고, 각 이슈는 키워드 최대 3개 + ('제목:...')."""
    issues = []
    for block in re.split(r"(?m)^\s*-{3,}\s*$", body):
        lines = block.splitlines()
        # '제목: ...' 줄이 있으면 카드에 그 제목을 쓴다 (예: 제목: 창신메모리발 메모리주 급락)
        title = next((ln.split(":", 1)[1].strip() for ln in lines if re.match(r"\s*제목\s*:", ln)), "")
        rest = "\n".join(ln for ln in lines if not re.match(r"\s*제목\s*:", ln))
        kws = [k.strip() for k in re.split(r"[,\n]", rest) if k.strip()][:3]
        if kws:
            issues.append(kws + ([f"제목:{title}"] if title else []))
    return issues[:2]


def issue_keywords(today: str) -> list[list[str]]:
    """사이트(관리자)에서 입력해 main에 올린 오늘의 핵심 이슈 키워드: issue_keywords/YYYYMMDD-HHMM.txt 중 오늘 마지막 파일.
    파일 하나가 그 시점의 전체 지정(최대 2개 이슈). 빈 파일이면 지정 해제."""
    if git("fetch", "-q", "origin", "main:refs/remotes/origin/main").returncode:
        return []
    names = [n for n in git("ls-tree", "--name-only", "origin/main", "issue_keywords/").stdout.decode().split()
             if n.split("/")[-1].startswith(today)]
    if not names:
        return []
    return parse_issue_keywords(git("show", f"origin/main:{sorted(names)[-1]}").stdout.decode("utf-8", "replace"))


def drop_future_points(snap: dict, t: datetime) -> None:
    """KB 지수 분봉에 가끔 아직 오지 않은 시각의 점(전날 같은 시각 값으로 보이는 15:14 등)이 섞여 온다
    → 미니차트가 그 시각까지 늘어나며 선이 튄다. 오늘 자료에서 지금 시각보다 뒤의 점은 버린다."""
    ix = snap.get("/api/indices/intraday") or {}
    today, hm = t.strftime("%Y%m%d"), t.strftime("%H%M")
    for code, d in ix.items():
        pts = d.get("points") or []
        if str(d.get("date")) == today and pts:
            keep = [p for p in pts if str(p.get("t", "")) <= hm]
            if len(keep) != len(pts):
                print("  미래 시각 분봉 제거:", code, len(pts) - len(keep), "개", flush=True)
                d["points"] = keep


def keep_previous(snap: dict, remote: dict | None, today: str) -> None:
    """KB 응답이 가끔 비거나 짧게 오면(예: 코스닥 분봉 0개) 화면 차트가 빈다 → 직전 스냅샷 값을 이어 쓴다."""
    if not remote or remote.get("date") != today:
        return
    old = remote.get("data") or {}
    for k, v in old.items():
        if k not in snap:
            snap[k] = v
    new_ix, old_ix = snap.get("/api/indices/intraday") or {}, old.get("/api/indices/intraday") or {}
    for code, prev in old_ix.items():
        cur = new_ix.get(code) or {}
        # 장 시작 전 스냅샷에는 전날 한 장(391개)이 들어 있다 → 날짜가 더 새로우면 점 수가 적어도 오늘 것을 쓴다
        if str(cur.get("date") or "") > str(prev.get("date") or ""):
            continue
        if len(cur.get("points") or []) < len(prev.get("points") or []):
            new_ix[code] = prev
    if new_ix:
        snap["/api/indices/intraday"] = new_ix


_kicked: dict = {"day": "", "slots": set()}
CONSENSUS_SLOTS = (("08:40", "09:40"), ("21:00", "22:00"))  # (시작, 이 시각 넘으면 건너뜀)


def kick_consensus(t: datetime) -> None:
    """평일 08:40·21:00에 목표주가 변동 리포트(consensus-report.yml)를 띄운다.
    아침 판은 전날 밤 이후 새로 나온 리포트, 밤 판은 오후 발간분까지 더한 그날 전체.
    이 작업은 이어달리기로 늘 떠 있어서 GitHub 예약(몇 시간씩 늦음)이나 루틴보다 확실하다."""
    hm, d = t.strftime("%H:%M"), t.strftime("%Y-%m-%d")
    if t.weekday() >= 5:
        return
    if _kicked["day"] != d:
        _kicked["day"], _kicked["slots"] = d, set()
    due = [a for a, b in CONSENSUS_SLOTS if a <= hm < b and a not in _kicked["slots"]]
    if not due:
        return
    _kicked["slots"].update(due)
    try:
        r = subprocess.run(["gh", "workflow", "run", "consensus-report.yml", "--ref", "main"], cwd=ROOT,
                           capture_output=True, timeout=60)
        print(hm, "목표주가 리포트 실행 요청:", r.returncode, r.stderr.decode(errors="replace")[-200:], flush=True)
    except Exception as e:
        print(hm, "목표주가 리포트 실행 요청 실패:", type(e).__name__, e, flush=True)


_ew = {"last": ""}


def earnings_tick(t: datetime) -> None:
    """10분마다 실적 속보 감시(earnings_watch.py): earnings-live 브랜치 갱신 → 텔레그램 알림.
    GitHub 예약(10분)이 하루 몇 번밖에 안 돌아 알림이 몇 시간씩 늦었기 때문에 늘 떠 있는 이 작업에서 돌린다."""
    key = t.strftime("%Y%m%d%H") + str(t.minute // 10)
    if _ew["last"] == key:
        return
    _ew["last"] = key
    out = ROOT / "out"
    for n in ("earnings.json", "messages.json"):
        (out / n).unlink(missing_ok=True)
    git("fetch", "-q", "origin", "+earnings-live:refs/remotes/origin/earnings-live")
    r = subprocess.run([sys.executable, "earnings_watch.py"], cwd=ROOT, capture_output=True, timeout=300)
    log = (r.stdout + r.stderr).decode(errors="replace").strip().splitlines()
    print(t.strftime("%H:%M"), "실적 감시:", r.returncode, log[-1] if log else "", flush=True)
    if (out / "earnings.json").exists():
        blob = git("hash-object", "-w", "--stdin", inp=(out / "earnings.json").read_bytes()).stdout.decode().strip()
        tree = git("mktree", inp=f"100644 blob {blob}\tearnings.json\n".encode()).stdout.decode().strip()
        commit = git("commit-tree", tree, "-m", f"실적 속보 {t:%Y-%m-%d %H:%M}").stdout.decode().strip()
        res = git("push", "-f", "-q", "origin", f"{commit}:refs/heads/earnings-live")
        if res.returncode:  # 저장 못 했으면 알림도 보내지 않는다 (다음에 다시 감지해 보냄)
            print("  earnings-live 올리기 실패:", res.stderr.decode(errors="replace")[:200], flush=True)
            return
    r = subprocess.run([sys.executable, "earnings_watch.py", "--send"], cwd=ROOT, capture_output=True, timeout=120)
    if r.stdout.strip():
        print("  ", r.stdout.decode(errors="replace").strip()[-200:], flush=True)


def main() -> None:
    """한 번 실행은 최대 MAX_RUN. 장 시간이 아니면 기다리고, 끝나면 워크플로가 다음 실행을 이어 띄운다(이어달리기).
    그래서 예약 실행이 늦거나 시작 루틴이 실패해도 매일 08:00부터 돈다."""
    began = now()
    proc: subprocess.Popen | None = None
    flows: dict = {}
    last_map: dict | None = None
    last_kws: list[str] | None = None
    day = ""
    misses = 0  # 서버가 /api/market 을 못 준 횟수 (이어지면 서버를 다시 띄운다)
    seeded = archived = False
    try:
        while now() - began < MAX_RUN:
            t = now()
            hm, today = t.strftime("%H:%M"), t.strftime("%Y%m%d")
            kick_consensus(t)
            try:
                earnings_tick(t)
            except Exception as e:
                print(hm, "실적 감시 실패:", type(e).__name__, e, flush=True)
            if t.weekday() >= 5 or not (START <= hm <= END):
                if proc:  # 장 끝: 서버를 내렸다가 다음 날 새로 띄운다 (KB 접속 토큰도 새로)
                    proc.terminate()
                    proc = None
                    print(hm, "장 시간 끝 → 다음 장까지 대기")
                time.sleep(60)
                continue
            if today != day:
                day, flows, seeded, archived = today, {}, False, False
            if proc is None or proc.poll() is not None:  # 서버가 꺼졌거나 죽었으면 다시 띄운다
                try:
                    proc = start_server()
                    misses = 0
                except RuntimeError as e:
                    print(hm, "서버 시작 실패, 5분 뒤 다시:", e)
                    time.sleep(300)
                    continue
            try:
                snap = snapshot()
                # 서버가 답을 못 주는데 직전 값을 그대로 다시 올리면 시각만 바뀌고 수치는 멈춘 화면이 된다
                # → 올리지 않고(화면의 기준 시각이 멈춰 이상을 알 수 있다) 세 번 이어지면 서버를 다시 띄운다
                if "/api/market" not in snap:
                    misses += 1
                    print(hm, f"시장 데이터 못 받음({misses}회) → 올리지 않음", flush=True)
                    if misses >= 3 and proc:
                        proc.terminate()
                        proc = None
                        print(hm, "서버 다시 띄움", flush=True)
                    time.sleep(20)
                    continue
                misses = 0
                remote, keep = remote_tree()
                if not seeded:
                    flows, seeded = seed_flows(remote, today), True
                    print("이어 받은 분 단위 기록:", len(flows))
                extra = {}
                if t.minute % 5 == 0 or last_map is None or not any(r.endswith("\tmap.json") for r in keep):
                    m = market_map()
                    if m:  # 맵은 따로 map.json (용량이 커서 1분마다 받는 snapshot.json과 나눈다)
                        last_map = m
                        extra["map.json"] = json.dumps({"date": today, "time": hm, "data": m}, ensure_ascii=False)
                wp = watch_prices()  # 관심 종목 현재가 1분마다
                if wp:
                    extra["prices.json"] = json.dumps({"date": today, "time": hm, "data": wp}, ensure_ascii=False)
                kws = issue_keywords(today)
                if t.minute % 5 == 0 or kws != last_kws or not any(r.endswith("\tissues.json") for r in keep):
                    if kws != last_kws:
                        print(hm, "핵심 이슈 키워드:", kws or "없음", flush=True)
                    last_kws = kws
                    iss = market_issues(last_map, kws)  # 오늘의 핵심 이슈 (지정 키워드 + 주요뉴스 묶음, 관련 종목 등락)
                    if iss is not None:
                        extra["issues.json"] = json.dumps({"date": today, "time": hm, "data": iss}, ensure_ascii=False)
                drop_future_points(snap, t)
                keep_previous(snap, remote, today)
                drop_future_points(snap, t)  # 직전 스냅샷에서 이어 받은 값에도
                record_flows(snap, flows, t)
                if not archived and t.strftime("%H%M") > "1546" and has_today(snap, today):
                    archived = save_archive(snap, t)
                if not has_today(snap, today):
                    if extra and remote:  # 개장 전: 수급(snapshot.json)은 그대로 두고 핵심 이슈·맵만 올린다
                        push(json.dumps(remote, ensure_ascii=False), keep, extra)
                    print(hm, "오늘 장 데이터 없음 (개장 전·휴장) → 수급은 올리지 않음", "· 이슈/맵 갱신" if extra and remote else "")
                elif pc_active(remote, t):
                    print(hm, "회사 PC가 올리는 중 → 건너뜀")
                else:
                    push(json.dumps({"date": today, "time": hm, "source": "gh", "data": snap}, ensure_ascii=False), keep, extra)
                    print(hm, "업로드", len(snap), "개 항목")
            except Exception as e:  # 한 번 실패해도 다음 분에 다시
                print(hm, "실패:", type(e).__name__, e)
            time.sleep(max(5, 60 - now().second))
    finally:
        if proc:
            proc.terminate()

if __name__ == "__main__":
    main()
