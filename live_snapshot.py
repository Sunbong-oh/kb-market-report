"""장중 실시간 수급 (GitHub Actions용 예비): 회사 PC가 꺼져 있어도 사이트가 실시간 수급을 보여 주게 한다.

평일 08:00~16:05(KST) 1분마다 리포트 화면이 쓰는 API 응답을 모아 flow-live 브랜치의 snapshot.json을 갱신한다.
- 서버는 리포트 전용 모드(REPORT_ONLY)로 잠깐 켠다 (report.py와 같은 방식).
- 회사 PC가 최근 3분 안에 올린 스냅샷이 있으면 PC 것이 더 정확하므로(선물 분 단위 기록 포함) 건너뛴다.
- PC가 올린 futures_flow.json은 그대로 둔다.
- 오늘 장 데이터가 아직 없으면(개장 전·휴장일) 올리지 않는다 → 사이트는 전일 장마감을 그대로 보여 준다.
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


def main() -> None:
    began = now()
    if began.weekday() >= 5 or began.strftime("%H:%M") > END:
        print("장중이 아니어서 종료")
        return
    proc = start_server()
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
