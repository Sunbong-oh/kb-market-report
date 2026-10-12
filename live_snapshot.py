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
한 번 실행은 최대 5시간 45분 (Actions 작업 시간 제한) - 워크플로가 하루 여러 번 이어서 띄운다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8000"
KST = timezone(timedelta(hours=9))
BRANCH = "flow-live"
KEYS = ("/api/status", "/api/market", "/api/macro", "/api/indices/intraday", "/api/themes", "/api/futures?minutes=1")
START, END = "08:00", "16:05"
MAX_RUN = timedelta(hours=5, minutes=45)
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


def push(data: str, keep_rows: list[str]) -> None:
    blob = git("hash-object", "-w", "--stdin", inp=data.encode("utf-8")).stdout.decode().strip()
    rows = "".join(r + "\n" for r in keep_rows) + f"100644 blob {blob}\tsnapshot.json\n"
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


def main() -> None:
    began = now()
    if began.weekday() >= 5 or began.strftime("%H:%M") > END:
        print("장중이 아니어서 종료")
        return
    proc = start_server()
    flows: dict = {}
    seeded = archived = False
    try:
        while True:
            t = now()
            hm = t.strftime("%H:%M")
            if hm > END or t - began > MAX_RUN:
                break
            if hm >= START:
                snap = snapshot()
                today = t.strftime("%Y%m%d")
                remote, keep = remote_tree()
                if not seeded:
                    flows, seeded = seed_flows(remote, today), True
                    print("이어 받은 분 단위 기록:", len(flows))
                record_flows(snap, flows, t)
                if not archived and t.strftime("%H%M") > "1546" and has_today(snap, today):
                    archived = save_archive(snap, t)
                if not has_today(snap, today):
                    print(hm, "오늘 장 데이터 없음 (개장 전·휴장) → 올리지 않음")
                elif pc_active(remote, t):
                    print(hm, "회사 PC가 올리는 중 → 건너뜀")
                else:
                    push(json.dumps({"date": today, "time": hm, "source": "gh", "data": snap}, ensure_ascii=False), keep)
                    print(hm, "업로드", len(snap), "개 항목")
            time.sleep(max(5, 60 - now().second))
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
