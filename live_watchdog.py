"""장중 수급 수집(live-snapshot) 감시.

평일 08:15~16:10 에 GitHub 서버가 5분마다 이 파일을 돌려, flow-live/snapshot.json 이 최신인지 본다.
  - 6분 넘게 갱신이 없으면:
      · 대기 중·실행 중인 수집 작업이 하나도 없으면 → 수집 작업을 다시 띄운다(workflow_dispatch).
      · 실행 중인데 값이 안 올라오고(10분↑) 작업이 12분 넘게 돌았으면 → 취소하고 다시 띄운다.
      · 대기만 하고 PC 실행기가 안 받으면(6분↑) → 텔레그램으로 알린다(PC 꺼짐·실행기 창 닫힘).
  - 다시 띄우거나 이상이 길어지면 텔레그램으로 알린다.
휴장일은 건너뛴다(네이버 오늘 지수 분봉이 없으면 휴장, 09:10 이후 판단 + 고정 휴장일 목록).
표준 라이브러리만 쓴다. 환경변수: GITHUB_TOKEN, GITHUB_REPOSITORY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DRY=1(보내거나 띄우지 않고 출력만).
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
REPO = os.environ.get("GITHUB_REPOSITORY") or "Sunbong-oh/kb-market-report"
WORKFLOW = "live-snapshot.yml"
OPEN, CLOSE = "08:15", "16:10"
START_HM = "08:00"             # 수집 시작 시각: 오늘 기록이 아직 없으면 여기서부터 멈춘 시간을 센다
STALE_MIN = 6                  # 이만큼 갱신이 없으면 이상
HUNG_MIN = 10                  # 실행 중인데 이만큼 값이 없으면 멈춘 것으로 본다
RUN_MIN_AGE = 12               # 시작한 지 이만큼 안 된 작업은 취소하지 않는다(시작에 3~5분 걸림)
HOLIDAYS = {"2026-12-25", "2026-12-31", "2027-01-01"}  # 고정 휴장일(매년 갱신). 그 밖의 날은 네이버 분봉으로 판단
DRY = os.environ.get("DRY") == "1"
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def http(url: str, method: str = "GET", data: dict | None = None, accept: str = "application/vnd.github+json", timeout: int = 30):
    headers = {"User-Agent": "live-watchdog", "Accept": accept}
    if TOKEN and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {TOKEN}"
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw


def get_json(url: str, accept: str = "application/vnd.github+json"):
    return json.loads(http(url, accept=accept).decode("utf-8"))


def snapshot_time(now: datetime) -> tuple[datetime | None, str]:
    """flow-live 의 snapshot.json 이 마지막으로 올라온 시각(KST). 오늘 것이 아니면 None."""
    today = f"{now:%Y%m%d}"
    try:
        j = get_json(f"https://api.github.com/repos/{REPO}/contents/snapshot.json?ref=flow-live", accept="application/vnd.github.raw")
    except Exception:  # 인증 없이/큰 파일 등으로 실패하면 raw 로 한 번 더
        j = get_json(f"https://raw.githubusercontent.com/{REPO}/flow-live/snapshot.json?t={int(now.timestamp())}")
    day, hm = str(j.get("date") or ""), str(j.get("time") or "")
    if day != today or len(hm) < 5:
        return None, f"{day} {hm}"
    return datetime.strptime(f"{day} {hm[:5]}", "%Y%m%d %H:%M").replace(tzinfo=KST), f"{day} {hm}"


def market_closed(now: datetime) -> bool:
    """오늘이 휴장일인가. 09:10 이후에만 네이버 오늘 분봉(없으면 전 거래일 것이 와서 날짜가 다름)으로 판단하고, 그 전엔 열린 것으로 본다."""
    if f"{now:%Y-%m-%d}" in HOLIDAYS:
        return True
    if f"{now:%H:%M}" < "09:10":
        return False
    try:
        rows = get_json("https://api.stock.naver.com/chart/domestic/index/KOSPI/minute", accept="application/json")
        last = str((rows[-1] if rows else {}).get("localDateTime") or "")[:8]
        return bool(last) and last != f"{now:%Y%m%d}"
    except Exception as exc:
        print(f"휴장 판단 실패(열린 것으로 봄): {exc}")
        return False


def active_runs() -> list[dict]:
    out = []
    for st in ("in_progress", "queued"):
        j = get_json(f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/runs?status={st}&per_page=10")
        out += [{"id": r["id"], "status": r["status"], "created": parse_ts(r["created_at"]), "started": parse_ts(r.get("run_started_at") or r["created_at"])}
                for r in j.get("workflow_runs", [])]
    return out


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(KST)


def dispatch(force: bool = False) -> None:
    """수집 작업을 띄운다. force=True 면 같은 그룹의 죽은 작업을 취소하고 시작(live-snapshot.yml 의 force 입력)."""
    if DRY:
        print(f"[DRY] 수집 작업 다시 띄움 force={force}")
        return
    http(f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches", "POST", {"ref": "main", "inputs": {"force": "true" if force else "false"}})


def cancel(run_id: int) -> None:
    if DRY:
        print(f"[DRY] 작업 {run_id} 취소")
        return
    http(f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/cancel", "POST", {})


def telegram(text: str) -> None:
    print("알림:", text.replace("\n", " / "))
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if DRY or not token or not chat:
        return
    q = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode("utf-8")
    try:
        urllib.request.urlopen(urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=q), timeout=20).read()
    except Exception as exc:
        print(f"텔레그램 전송 실패: {exc}")


def decide(now: datetime, last: datetime | None, runs: list[dict]) -> dict:
    """무엇을 할지 정한다(순수 함수: 시험하기 쉽게). 반환: {stale, restart, cancel, alert}"""
    base = last or datetime.strptime(f"{now:%Y%m%d} {START_HM}", "%Y%m%d %H:%M").replace(tzinfo=KST)
    stale = max(0.0, (now - base).total_seconds() / 60)
    res = {"stale": stale, "restart": False, "cancel": [], "alert": None}
    if stale < STALE_MIN:
        return res
    running = [r for r in runs if r["status"] == "in_progress"]
    queued = [r for r in runs if r["status"] != "in_progress"]
    when = f"{now:%H:%M}"
    seen = f"마지막 기록 {last:%H:%M}" if last else "오늘 기록 없음"
    if not runs:
        res["restart"] = True
        res["alert"] = f"⚠️ 장중 수급 수집이 멈춰 있어 다시 띄웠어요 ({when}, {seen}).\n회사 PC 실행기 창이 켜져 있어야 시작돼요."
    elif running:
        old = [r for r in running if (now - r["started"]).total_seconds() / 60 >= RUN_MIN_AGE]
        if stale >= HUNG_MIN and old:
            res["cancel"] = [r["id"] for r in old]
            res["restart"] = True
            res["alert"] = f"⚠️ 수집 작업이 돌고 있는데 {int(stale)}분째 값이 안 올라와 취소하고 다시 띄웠어요 ({when}, {seen})."
        elif 20 <= stale < 26:
            res["alert"] = f"⚠️ 수집 작업은 돌고 있는데 값이 {int(stale)}분째 안 올라와요 ({when}, {seen}). 회사 PC를 확인해 주세요."
    else:  # 대기만 하고 실행기가 안 받는다
        age = max((now - r["created"]).total_seconds() / 60 for r in queued)
        if 6 <= age < 11 or 20 <= age < 26:
            res["alert"] = f"⚠️ 수집 작업이 {int(age)}분째 대기 중이에요 ({when}, {seen}).\n회사 PC 실행기가 꺼졌거나 창이 닫혔을 수 있어요. PC에서 실행기(run.cmd) 창을 확인해 주세요."
    return res


def main() -> int:
    now = datetime.now(KST)
    if os.environ.get("NOW"):  # 시험용: NOW="2026-11-16 08:30"
        now = datetime.strptime(os.environ["NOW"], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    hm = f"{now:%H:%M}"
    if now.weekday() >= 5 or not (OPEN <= hm <= CLOSE):
        print(f"{now:%Y-%m-%d %H:%M} 감시 시간 아님")
        return 0
    if market_closed(now):
        print("휴장일 → 건너뜀")
        return 0
    last, raw = snapshot_time(now)
    runs = active_runs()
    d = decide(now, last, runs)
    print(f"{now:%H:%M} 기록 {raw} · 멈춘 지 {d['stale']:.0f}분 · 작업 {[(r['id'], r['status']) for r in runs]} → 재시작 {d['restart']} 취소 {d['cancel']}")
    for rid in d["cancel"]:
        cancel(rid)
    if d["restart"]:
        dispatch(force=bool(runs))  # 실행 중으로 남은(죽은) 작업이 있으면 그것을 취소하고 시작
    if d["alert"]:
        telegram(d["alert"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
