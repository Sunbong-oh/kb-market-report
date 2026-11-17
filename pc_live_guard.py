"""회사 PC 에서 도는 장중 수급 수집 지킴이 (GitHub 예약에 의존하지 않는다).

PC 가 켜진 뒤 평일 08:10~16:10 에 1분마다:
  1) GitHub 실행기(actions-runner, -runner2, -runner3)의 창이 닫혀 있으면 run.cmd 를 다시 켠다.
  2) flow-live 가 5분 넘게 갱신이 없으면 수집(live-snapshot)을 다시 띄운다.
       · 수집 작업이 하나도 없으면 → 그냥 띄운다.
       · 12분 넘게 '진행 중'인데 값이 안 올라오면(PC 가 꺼져 죽은 작업) → force=true 로 띄워 죽은 작업을 취소하고 시작한다.
       · 대기만 하고 8분 넘게 시작 못 하면 → force=true 로 띄운다.
  띄우기는 7분에 한 번까지만. 휴장일은 건너뛴다(고정 목록 + 09:10 이후 네이버 오늘 분봉 확인).
GitHub 와는 gh(이 PC 에 로그인돼 있어야 함)로만 통신한다. 기록: %TEMP%\\pc_live_guard.log
실행: python pc_live_guard.py   (pc_start_live.bat 이 PC 가 켜질 때 최신 파일을 받아 실행)
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
REPO = "Sunbong-oh/kb-market-report"
WORKFLOW = "live-snapshot.yml"
IS_WIN = os.name == "nt"
GH = r"C:\Program Files\GitHub CLI\gh.exe" if IS_WIN else "gh"
HOME = os.path.expanduser("~")
RUNNER_DIRS = [os.path.join(HOME, n) for n in ("actions-runner", "actions-runner2", "actions-runner3")]
OPEN, CLOSE = "08:10", "16:10"
STALE_MIN, HUNG_STALE_MIN, HUNG_MIN, QUEUED_MIN, COOLDOWN_MIN = 5, 10, 12, 8, 7
HOLIDAYS = {"2026-12-25", "2026-12-31", "2027-01-01"}  # 고정 휴장일(매년 갱신). 그 밖의 날은 네이버 분봉으로 판단
LOG = os.path.join(tempfile.gettempdir(), "pc_live_guard.log")


def log(msg: str) -> None:
    line = f"[{datetime.now(KST):%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def gh(*args: str, timeout: int = 60) -> str | None:
    try:
        r = subprocess.run([GH, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except Exception as exc:
        log(f"gh 실행 실패: {exc}")
        return None
    if r.returncode != 0:
        log(f"gh 오류({' '.join(args[:3])}): {(r.stderr or r.stdout).strip()[:200]}")
        return None
    return r.stdout


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s.strip(), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(KST)


def last_update() -> datetime | None:
    """flow-live 브랜치의 마지막 갱신 시각(수집 작업은 1분마다 여기에 커밋한다)."""
    out = gh("api", f"repos/{REPO}/commits/flow-live", "--jq", ".commit.committer.date")
    try:
        return parse_utc(out) if out else None
    except ValueError:
        return None


def active_runs() -> list[dict] | None:
    out = gh("run", "list", "-R", REPO, "--workflow", WORKFLOW, "--limit", "10", "--json", "databaseId,status,createdAt,startedAt")
    if out is None:
        return None
    runs = []
    for r in json.loads(out or "[]"):
        if r["status"] in ("in_progress", "queued", "pending", "waiting", "requested"):
            created = parse_utc(r["createdAt"])
            started = created
            if r["status"] == "in_progress":  # run 의 startedAt 은 '대기 시작' 시각이라 작업(job)이 실제로 시작된 시각을 따로 읽는다
                jo = gh("run", "view", str(r["databaseId"]), "-R", REPO, "--json", "jobs", "--jq", "[.jobs[].startedAt | select(. != null)] | min // empty")
                try:
                    started = parse_utc(jo) if jo and jo.strip() else created
                except ValueError:
                    started = created
            runs.append({"id": r["databaseId"], "status": r["status"], "created": created, "started": started})
    return runs


def dispatch(force: bool, why: str) -> bool:
    log(f"수집 작업 띄움(force={force}): {why}")
    return gh("workflow", "run", WORKFLOW, "-R", REPO, "--ref", "main", "-f", f"force={'true' if force else 'false'}") is not None


def listeners() -> list[str]:
    """실행 중인 Runner.Listener.exe 경로 목록(소문자)."""
    if not IS_WIN:
        return []
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_Process -Filter \"Name='Runner.Listener.exe'\").ExecutablePath"],
                           capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        return [x.strip().lower() for x in r.stdout.splitlines() if x.strip()]
    except Exception as exc:
        log(f"실행기 목록 확인 실패: {exc}")
        return []


def ensure_runners() -> None:
    """창이 닫혀 꺼진 실행기를 다시 켠다(이미 떠 있는 것은 건드리지 않는다)."""
    if not IS_WIN:
        return
    alive = listeners()
    if not alive and not any(os.path.exists(os.path.join(d, "run.cmd")) for d in RUNNER_DIRS):
        return
    for d in RUNNER_DIRS:
        cmd = os.path.join(d, "run.cmd")
        if not (os.path.exists(cmd) and os.path.exists(os.path.join(d, ".runner"))):
            continue
        if any(p.startswith(d.lower() + "\\") for p in alive):
            continue
        log(f"실행기 꺼져 있음 → 다시 켬: {d}")
        subprocess.Popen(["cmd", "/c", "start", "", "/min", cmd], cwd=d, close_fds=True)


def market_closed(now: datetime) -> bool:
    if f"{now:%Y-%m-%d}" in HOLIDAYS:
        return True
    if f"{now:%H:%M}" < "09:10":
        return False
    try:
        req = urllib.request.Request("https://api.stock.naver.com/chart/domestic/index/KOSPI/minute", headers={"User-Agent": "Mozilla/5.0"})
        rows = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
        last = str((rows[-1] if rows else {}).get("localDateTime") or "")[:8]
        return bool(last) and last != f"{now:%Y%m%d}"
    except Exception as exc:
        log(f"휴장 판단 실패(열린 것으로 봄): {exc}")
        return False


def decide(now: datetime, last: datetime | None, runs: list[dict]) -> tuple[bool, str] | None:
    """(force 여부, 이유) 또는 None(할 일 없음). 순수 함수."""
    base = datetime.strptime(f"{now:%Y%m%d} 08:00", "%Y%m%d %H:%M").replace(tzinfo=KST)  # 오늘 아침 이전 기록이면 08:00 부터 센다
    stale = (now - max(last, base)).total_seconds() / 60 if last else 999
    if stale < STALE_MIN:
        return None
    running = [r for r in runs if r["status"] == "in_progress"]
    queued = [r for r in runs if r["status"] != "in_progress"]
    if not runs:
        return False, f"{int(stale)}분째 갱신 없음, 작업 없음"
    hung = [r for r in running if (now - r["started"]).total_seconds() / 60 >= HUNG_MIN] if stale >= HUNG_STALE_MIN else []
    if hung:
        return True, f"{int(stale)}분째 갱신 없음, 작업 {hung[0]['id']} 이 {int((now - hung[0]['started']).total_seconds() / 60)}분째 진행 중(죽은 작업)"
    if running:
        return None  # 방금 시작한 작업: 첫 올리기까지 3~5분
    age = max((now - r["created"]).total_seconds() / 60 for r in queued)
    if age >= QUEUED_MIN:
        return True, f"{int(stale)}분째 갱신 없음, 작업이 {int(age)}분째 대기만 함"
    return None


def main() -> int:
    log(f"지킴이 시작 (python {sys.version.split()[0]}, gh={GH})")
    if gh("auth", "status") is None:
        log("gh 로그인이 안 돼 있어요: gh auth login 을 먼저 해 주세요")
        return 1
    last_dispatch: datetime | None = None
    pre_open_tries = 0
    while True:
        now = datetime.now(KST)
        hm = f"{now:%H:%M}"
        if now.weekday() >= 5 or hm > CLOSE:
            log("감시 시간 끝 → 종료")
            return 0
        if hm >= OPEN:
            try:
                ensure_runners()
                if not market_closed(now):
                    runs, last = active_runs(), last_update()
                    if runs is not None and last is not None:
                        d = decide(now, last, runs)
                        cooled = last_dispatch is None or (now - last_dispatch).total_seconds() / 60 >= COOLDOWN_MIN
                        if d and cooled and not (hm < "09:10" and pre_open_tries >= 2):
                            if dispatch(*d):
                                last_dispatch = now
                                pre_open_tries += 1 if hm < "09:10" else 0
            except Exception as exc:
                log(f"점검 중 오류(계속함): {type(exc).__name__}: {exc}")
        time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
